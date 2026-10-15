"""Description generation pipeline: prompt -> LLM -> glossary -> SEO/channel score -> quality checks -> version.

LLM calls run in a thread pool (they are I/O bound); all database writes happen on the
coordinating thread so SQLite and PostgreSQL behave the same way.
"""

import logging
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    Catalog,
    DescriptionVersion,
    GenerationJob,
    GlossaryTerm,
    JobStatus,
    Organization,
    Product,
    ReviewStatus,
    StylePreset,
    User,
)
from app.services import email, glossary, images, notifications, quality, refine, seo, vision, webhooks
from app.services.llm import (
    GeneratedContent,
    GenerationResult,
    LLMError,
    LLMProvider,
    LLMQuotaExceeded,
    concurrency_for,
    estimate_cost,
    get_provider,
    provider_for_org,
    vision_provider_for,
)
from app.services.prompts import LANGUAGES, ProductInput, StyleSpec, build_system_prompt, build_user_prompt

log = logging.getLogger(__name__)

# Rough per-request token usage, used only for the pre-job cost estimate.
EST_INPUT_TOKENS = 1200
EST_OUTPUT_TOKENS = 1500
EST_IMAGE_TOKENS = 1500


def now() -> datetime:
    return datetime.now(timezone.utc)


def product_input(product: Product, with_photos: bool = True) -> ProductInput:
    return ProductInput(
        photo_analysis=vision.cached(product.images, product.image_analysis) if with_photos else None,
        sku=product.sku,
        name=product.name,
        category=product.category,
        brand=product.brand,
        price=product.price,
        attributes=product.attributes or {},
        keywords=product.keywords or [],
        original_description=product.original_description,
    )


def style_spec(preset: StylePreset | None) -> StyleSpec:
    if preset is None:
        return StyleSpec()
    return StyleSpec(
        tone=preset.tone, length=preset.length, instructions=preset.instructions,
        examples=preset.examples or [], channel=preset.channel or "generic",
    )


def glossary_terms(db: Session, org_id: int) -> list[glossary.Term]:
    rows = db.scalars(select(GlossaryTerm).where(GlossaryTerm.org_id == org_id).order_by(GlossaryTerm.term))
    return [glossary.Term(term=r.term, kind=r.kind, replacement=r.replacement) for r in rows]


def monthly_usage(db: Session, org_id: int) -> int:
    month_start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count(DescriptionVersion.id)).where(
            DescriptionVersion.org_id == org_id,
            DescriptionVersion.source == "ai",
            DescriptionVersion.created_at >= month_start,
        )
    ) or 0


def remaining_quota(db: Session, org: Organization) -> int:
    return max(0, org.monthly_generation_limit - monthly_usage(db, org.id))


def estimate_job(item_count: int, engine: str | None = None, images_per_item: float = 0) -> dict:
    provider = get_provider(engine)
    # Rough wall-clock: local models on a laptop CPU take minutes per item, cloud APIs seconds.
    seconds_per_item = {"ollama": 240, "mock": 1, "gemini": 8, "groq": 4}.get(provider.name, 20)
    input_tokens = (EST_INPUT_TOKENS + EST_IMAGE_TOKENS * images_per_item) * item_count
    return {
        "items": item_count,
        "model": provider.model,
        # Free tiers are paced to N requests per minute, which caps the speed whatever the concurrency.
        "estimated_minutes": round(max(item_count * seconds_per_item / concurrency_for(engine),
                                       item_count * getattr(provider, "min_interval", 0.0)) / 60, 1),
        "estimated_cost_usd": estimate_cost(provider.model, int(input_tokens), EST_OUTPUT_TOKENS * item_count),
    }


def _source_text(product: Product) -> str:
    return product_input(product).source_text()


def approved_examples(db: Session, org_id: int, catalog_id: int, language: str, exclude_product: int | None = None) -> list[str]:
    """Up to 2 descriptions the team approved in this catalog and language; hand-edited ones first.

    They show the model what this team accepts, so output moves toward the team's taste over time.
    """
    if not get_settings().learn_from_approved:
        return []
    query = (
        select(DescriptionVersion.long_description)
        .join(Product, Product.id == DescriptionVersion.product_id)
        .where(
            DescriptionVersion.org_id == org_id,
            Product.catalog_id == catalog_id,
            DescriptionVersion.language == language,
            DescriptionVersion.status == ReviewStatus.APPROVED,
            DescriptionVersion.is_current.is_(True),
        )
        .order_by((DescriptionVersion.source == "manual").desc(), DescriptionVersion.id.desc())
        .limit(2)
    )
    if exclude_product:
        query = query.where(DescriptionVersion.product_id != exclude_product)
    return [text for text in db.scalars(query) if text and len(text.split()) >= 15]


def save_version(
    db: Session,
    product: Product,
    language: str,
    content: GeneratedContent,
    *,
    source: str,
    length: str = "medium",
    channel: str = "generic",
    job_id: int | None = None,
    preset_id: int | None = None,
    user_id: int | None = None,
    model: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    terms: list[glossary.Term] | None = None,
    notes: list[str] | None = None,
) -> DescriptionVersion:
    """Store content as the new current version for (product, language), with SEO + quality data."""
    warnings: list[dict] = []
    if terms:
        content, replaced, present = glossary.apply(content, terms)
        if replaced:
            warnings.append({"type": "glossary_fixed", "message": "Glossary replacements applied: " + ", ".join(replaced)})
        if present:
            warnings.append({"type": "glossary", "message": "Contains words your glossary says to avoid: "
                             + ", ".join(present)})

    score, report = seo.score_content(
        title=content.title,
        short_description=content.short_description,
        long_description=content.long_description,
        bullet_points=content.bullet_points,
        meta_title=content.meta_title,
        meta_description=content.meta_description,
        keywords=product.keywords or [],
        language=language,
        length=length,
        channel=channel,
    )
    failed_channel = [c["label"] for c in report["checks"] if c["id"].startswith("ch_") and not c["passed"]]
    if failed_channel:
        warnings.append({"type": "channel_rules", "message": "Marketplace rules not met: " + "; ".join(failed_channel)})

    generated_text = " ".join([content.title, content.short_description, content.long_description,
                               *content.bullet_points, content.meta_description])
    if quality.wrong_language(content.long_description, language):
        warnings.append({
            "type": "wrong_language",
            "message": f"This does not appear to be written in {LANGUAGES.get(language, language)}. "
                       "Regenerate (Claude handles this language more reliably) or edit before approving.",
        })
    if source == "ai":
        numbers = quality.find_unsupported_numbers(generated_text, _source_text(product))
        if numbers:
            warnings.append({
                "type": "unverified_facts",
                "message": f"Numbers not found in product data: {', '.join(numbers)}. Verify before approving.",
            })
        analysis = vision.cached(product.images, product.image_analysis)
        claims = quality.find_unsupported_claims(
            generated_text, _source_text(product), allow_visual=bool(product.images) and analysis is None,
            appearance_only=(analysis or {}).get("looks_like_materials"))
        if claims:
            warnings.append({
                "type": "unverified_facts",
                "message": f"Not stated in the product data: {', '.join(claims)}. Verify before approving.",
            })
        style = quality.ai_style_issues(f"{content.short_description} {content.long_description}", language)
        if style:
            warnings.append({"type": "ai_style", "message": "Reads machine-written: " + "; ".join(style) + "."})
    photo = vision.cached(product.images, product.image_analysis)
    if source == "ai" and photo and photo.get("mismatch"):
        warnings.append({"type": "photo_mismatch", "message": "The photos may not match this product: " + photo["mismatch"]})
    for note in notes or []:
        warnings.append({"type": "note", "message": note})

    others = db.execute(
        select(Product.sku, DescriptionVersion.long_description)
        .join(Product, Product.id == DescriptionVersion.product_id)
        .where(
            Product.catalog_id == product.catalog_id,
            DescriptionVersion.language == language,
            DescriptionVersion.is_current.is_(True),
            DescriptionVersion.product_id != product.id,
        )
        .order_by(DescriptionVersion.id.desc())
        .limit(500)
    ).all()
    duplicates = quality.find_duplicates(
        content.long_description, [(sku, text) for sku, text in others],
        get_settings().duplicate_similarity_threshold,
    )
    if duplicates:
        warnings.append({
            "type": "duplicate_content",
            "message": "Very similar to: " + ", ".join(f"{d['sku']} ({d['similarity']:.0%})" for d in duplicates),
        })

    last_no = db.scalar(
        select(func.max(DescriptionVersion.version_no)).where(
            DescriptionVersion.product_id == product.id, DescriptionVersion.language == language
        )
    ) or 0
    db.execute(
        update(DescriptionVersion)
        .where(DescriptionVersion.product_id == product.id, DescriptionVersion.language == language)
        .values(is_current=False)
    )
    version = DescriptionVersion(
        org_id=product.org_id,
        product_id=product.id,
        job_id=job_id,
        language=language,
        version_no=last_no + 1,
        is_current=True,
        source=source,
        title=content.title,
        short_description=content.short_description,
        long_description=content.long_description,
        bullet_points=content.bullet_points,
        meta_title=content.meta_title,
        meta_description=content.meta_description,
        seo_score=score,
        seo_report=report,
        warnings=warnings,
        status=ReviewStatus.DRAFT,
        model=model,
        preset_id=preset_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        created_by=user_id,
    )
    db.add(version)
    db.flush()
    return version


def _call(
    provider: LLMProvider, system_prompt: str, product: ProductInput, language: str,
    image_refs: list[str], variant: int, total_variants: int, length: str = "medium", channel: str = "generic",
) -> GenerationResult:
    """Runs in a worker thread: load images (I/O), call the model, and revise a weak draft once."""
    # With a photo analysis the writer gets the analyst's report, not the photos themselves.
    loaded = images.load(image_refs) if image_refs and provider.supports_images and not product.photo_analysis else []
    user_prompt = build_user_prompt(product, language, variant=variant, total_variants=total_variants,
                                    image_count=len(loaded))
    result = provider.generate(system_prompt, user_prompt, images=loaded or None)
    settings = get_settings()
    if settings.auto_improve:
        visual = bool(loaded)
        first = refine.review(result.content, product, language, length, channel, visual)
        if refine.needs_revision(first, settings.auto_improve_min_score):
            try:
                revised = provider.generate(system_prompt, refine.revision_prompt(user_prompt, result.content, first.issues),
                                            images=loaded or None)
            except LLMError as exc:  # keep the first draft; a quota problem surfaces on the next product
                log.info("Revision skipped for %s: %s", product.sku, exc)
            else:
                second = refine.review(revised.content, product, language, length, channel, visual)
                tokens_in = result.input_tokens + revised.input_tokens
                tokens_out = result.output_tokens + revised.output_tokens
                if second.better_than(first):
                    revised.notes = [*result.notes, *revised.notes]
                    result = revised
                result.input_tokens, result.output_tokens = tokens_in, tokens_out
    if image_refs and not provider.supports_images and not product.photo_analysis and not result.notes:
        result.notes.append(f"{provider.model} cannot read images; generated from text data only")
    return result


def generate_single(
    db: Session,
    product: Product,
    language: str,
    preset: StylePreset | None,
    *,
    extra_instructions: str = "",
    user_id: int | None = None,
    engine: str | None = None,
    org: Organization | None = None,
    use_images: bool = True,
) -> DescriptionVersion:
    """Synchronous one-off generation (used by the "Regenerate" button)."""
    style = style_spec(preset)
    provider = provider_for_org(engine, org)
    terms = glossary_terms(db, product.org_id)
    if use_images:
        ensure_photo_analysis(db, product, engine, org)
    examples = approved_examples(db, product.org_id, product.catalog_id, language, exclude_product=product.id)
    system = build_system_prompt(style, language, extra_instructions, glossary.prompt_lines(terms), examples)
    result = _call(provider, system, product_input(product, with_photos=use_images), language,
                   product.images if use_images else [], 1, 1, style.length, style.channel)
    version = save_version(
        db, product, language, result.content, source="ai", length=style.length, channel=style.channel,
        preset_id=preset.id if preset else None, user_id=user_id, model=result.model,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens, terms=terms, notes=result.notes,
    )
    db.commit()
    return version


def plan_tasks(db: Session, product_ids: list[int], languages: list[str], only_missing: bool) -> list[tuple[int, str]]:
    """(product_id, language) pairs to generate; with only_missing, skip pairs that already have content."""
    tasks = [(pid, lang) for pid in product_ids for lang in languages]
    if not only_missing or not tasks:
        return tasks
    rows = db.execute(
        select(DescriptionVersion.product_id, DescriptionVersion.language).where(
            DescriptionVersion.product_id.in_(product_ids),
            DescriptionVersion.language.in_(languages),
            DescriptionVersion.is_current.is_(True),
        )
    ).all()
    existing = {(pid, lang) for pid, lang in rows}
    return [t for t in tasks if t not in existing]


def _pick_best_variants(db: Session, job: GenerationJob) -> None:
    """With several variants per product, make the highest-scoring one current."""
    versions = db.scalars(select(DescriptionVersion).where(DescriptionVersion.job_id == job.id)).all()
    groups: dict[tuple[int, str], list[DescriptionVersion]] = {}
    for v in versions:
        groups.setdefault((v.product_id, v.language), []).append(v)
    for (product_id, language), group in groups.items():
        if len(group) < 2:
            continue
        best = max(group, key=lambda v: (v.seo_score, -len(v.warnings or []), v.version_no))
        db.execute(
            update(DescriptionVersion)
            .where(DescriptionVersion.product_id == product_id, DescriptionVersion.language == language)
            .values(is_current=False)
        )
        best.is_current = True
    db.commit()


def _notify_finished(db: Session, job: GenerationJob) -> None:
    org = db.get(Organization, job.org_id)
    user = db.get(User, job.created_by) if job.created_by else None
    catalog = db.get(Catalog, job.catalog_id)
    if user and catalog:
        notifications.notify(db, job.org_id, [user.id], "job_finished",
                             f"Generation {job.status} for {catalog.name}",
                             f"{job.completed} done" + (f", {job.failed} failed" if job.failed else ""),
                             f"/catalogs/{catalog.id}")
    if not (org and org.notify_on_job_complete and user and user.is_active and catalog):
        return
    try:
        email.job_finished(user.email, user.full_name, catalog.name, catalog.id, job.completed, job.failed,
                           "completed" if job.status == JobStatus.COMPLETED else "failed")
    except Exception:
        log.exception("Could not send job notification for job %s", job.id)


def ensure_photo_analysis(db: Session, product: Product, engine: str | None, org: Organization | None,
                           force: bool = False) -> dict | None:
    """Analyse the product's photos unless an up-to-date analysis is saved. Returns the analysis (or None)."""
    if not product.images:
        return None
    if not force and (current := vision.cached(product.images, product.image_analysis)):
        return current
    provider = vision_provider_for(engine, org)
    if provider is None:
        return None
    analysis, _ = vision.analyze(provider, product_input(product, with_photos=False), list(product.images))
    if analysis:
        product.image_analysis = analysis
        db.commit()
    return analysis


def analyse_photos(db: Session, job: GenerationJob, products: list[Product], org: Organization | None) -> None:
    """Before writing, have a vision model study each product's photos (cached per photo set)."""
    todo = [p for p in products if p.images and not vision.cached(p.images, p.image_analysis)]
    if not todo:
        return
    provider = vision_provider_for(job.engine, org)
    if provider is None:
        return
    with ThreadPoolExecutor(max_workers=concurrency_for(job.engine)) as pool:
        futures = {pool.submit(vision.analyze, provider, product_input(p, with_photos=False), list(p.images)): p
                   for p in todo}
        for future in futures:
            product = futures[future]
            try:
                analysis, result = future.result()
            except Exception as exc:  # writing still works from the text data
                log.warning("Photo analysis failed for %s: %s", product.sku, exc)
                continue
            if analysis:
                product.image_analysis = analysis
                job.input_tokens += result.input_tokens
                job.output_tokens += result.output_tokens
                job.cost_usd = round(job.cost_usd + estimate_cost(result.model, result.input_tokens,
                                                                  result.output_tokens), 6)
                db.commit()


def run_job(job_id: int) -> None:
    """Process every (product, language, variant) of a generation job."""
    db = SessionLocal()
    try:
        job = db.get(GenerationJob, job_id)
        if job is None or job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            return
        job.status = JobStatus.RUNNING
        job.started_at = now()
        db.commit()

        preset = db.get(StylePreset, job.preset_id) if job.preset_id else None
        style = style_spec(preset)
        query = select(Product).where(Product.catalog_id == job.catalog_id).order_by(Product.id)
        if job.product_ids:
            query = query.where(Product.id.in_(job.product_ids))
        products = {p.id: p for p in db.scalars(query)}
        variants = max(1, min(3, job.variants or 1))
        tasks = [(pid, lang, v) for pid, lang in plan_tasks(db, list(products), job.languages, job.only_missing)
                 for v in range(1, variants + 1)]
        terms = glossary_terms(db, job.org_id)
        glossary_lines = glossary.prompt_lines(terms)
        system_prompts = {
            lang: build_system_prompt(style, lang, job.extra_instructions, glossary_lines,
                                      approved_examples(db, job.org_id, job.catalog_id, lang))
            for lang in job.languages
        }
        org = db.get(Organization, job.org_id)
        if job.use_images:
            analyse_photos(db, job, [products[pid] for pid in {t[0] for t in tasks}], org)
        inputs = {pid: product_input(p, with_photos=job.use_images) for pid, p in products.items()}
        image_refs = {pid: (list(p.images or []) if job.use_images else []) for pid, p in products.items()}
        provider = provider_for_org(job.engine, org)
        workers = concurrency_for(job.engine)

        job.total = len(tasks)
        db.commit()

        cancelled = quota_stopped = stop_submitting = False
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending: dict[Future, tuple[int, str, int]] = {}
            queue = iter(tasks)

            def submit_next() -> None:
                task = next(queue, None)
                if task is not None:
                    pid, lang, v = task
                    pending[pool.submit(_call, provider, system_prompts[lang], inputs[pid], lang,
                                        image_refs[pid], v, variants, style.length, style.channel)] = task

            for _ in range(workers * 2):
                submit_next()

            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    pid, lang, _variant = pending.pop(future)
                    product = products[pid]
                    try:
                        result: GenerationResult = future.result()
                        save_version(
                            db, product, lang, result.content, source="ai", length=style.length,
                            channel=style.channel, job_id=job.id, preset_id=job.preset_id,
                            user_id=job.created_by, model=result.model, input_tokens=result.input_tokens,
                            output_tokens=result.output_tokens, terms=terms, notes=result.notes,
                        )
                        job.completed += 1
                        job.input_tokens += result.input_tokens
                        job.output_tokens += result.output_tokens
                        job.cost_usd = round(
                            job.cost_usd + estimate_cost(result.model, result.input_tokens, result.output_tokens), 6
                        )
                    except Exception as exc:  # one bad product must not stop the batch
                        db.rollback()
                        job = db.get(GenerationJob, job_id)
                        log.warning("Generation failed for product %s (%s): %s", pid, lang, exc)
                        job.failed += 1
                        message = str(exc) if isinstance(exc, LLMError) else "Unexpected error"
                        job.errors = [*(job.errors or []), {"sku": product.sku, "language": lang, "error": message}][-200:]
                        if isinstance(exc, LLMQuotaExceeded) and not quota_stopped:
                            # Nothing more can succeed today: stop instead of failing every product.
                            quota_stopped = True
                            job.errors = [*job.errors, {"error": f"Job stopped: {message}"}]
                    db.commit()

                    db.refresh(job)
                    if job.status == JobStatus.CANCELLED or quota_stopped:
                        cancelled = job.status == JobStatus.CANCELLED
                        stop_submitting = True
                    if not stop_submitting:
                        submit_next()
            # Pending futures are drained above; nothing new is submitted after a cancel.

        if variants > 1:
            _pick_best_variants(db, job)
        job.finished_at = now()
        if not cancelled:
            nothing_done = job.total and (job.failed == job.total or (quota_stopped and job.completed == 0))
            job.status = JobStatus.FAILED if nothing_done else JobStatus.COMPLETED
        db.commit()
        if not cancelled:
            _notify_finished(db, job)
            webhooks.safe_emit(db, job.org_id, "job.completed" if job.status == JobStatus.COMPLETED else "job.failed", {
                "job_id": job.id, "catalog_id": job.catalog_id, "completed": job.completed, "failed": job.failed,
                "languages": job.languages, "cost_usd": job.cost_usd})
    except Exception:
        log.exception("Generation job %s crashed", job_id)
        db.rollback()
        job = db.get(GenerationJob, job_id)
        if job:
            job.status = JobStatus.FAILED
            job.finished_at = now()
            job.errors = [*(job.errors or []), {"error": "Job crashed unexpectedly; see server logs"}]
            db.commit()
    finally:
        db.close()
