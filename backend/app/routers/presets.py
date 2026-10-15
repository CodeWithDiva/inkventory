from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import can_edit, get_current_user, get_owned
from app.models import Catalog, GlossaryTerm, Organization, Product, StylePreset, User
from app.schemas import GlossaryIn, GlossaryOut, LearnVoiceIn, LearnVoiceOut, PresetIn, PresetOut

router = APIRouter(prefix="/presets", tags=["presets"])

BUILTIN_PRESETS = [
    {"name": "Professional", "tone": "professional", "length": "medium"},
    {"name": "Friendly & Casual", "tone": "friendly", "length": "medium"},
    {"name": "Luxury", "tone": "luxury", "length": "long"},
    {"name": "Technical Specs", "tone": "technical", "length": "medium"},
    {"name": "Short & Minimal", "tone": "minimalist", "length": "short"},
]


def seed_builtin_presets(db: Session) -> None:
    if db.scalar(select(StylePreset.id).where(StylePreset.org_id.is_(None)).limit(1)):
        return
    for preset in BUILTIN_PRESETS:
        db.add(StylePreset(org_id=None, **preset))
    db.commit()


def to_out(preset: StylePreset) -> PresetOut:
    out = PresetOut.model_validate(preset)
    out.is_builtin = preset.org_id is None
    return out


def get_usable_preset(db: Session, preset_id: int | None, user: User) -> StylePreset | None:
    """A preset the user may generate with: built-in or owned by their organization."""
    if preset_id is None:
        return None
    preset = db.get(StylePreset, preset_id)
    if preset is None or (preset.org_id is not None and preset.org_id != user.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Style preset not found")
    return preset


@router.get("", response_model=list[PresetOut])
def list_presets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    presets = db.scalars(
        select(StylePreset)
        .where(or_(StylePreset.org_id.is_(None), StylePreset.org_id == user.org_id))
        .order_by(StylePreset.org_id.is_(None).desc(), StylePreset.id)
    )
    return [to_out(p) for p in presets]


@router.post("", response_model=PresetOut, status_code=201)
def create_preset(body: PresetIn, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    preset = StylePreset(org_id=user.org_id, **body.model_dump())
    db.add(preset)
    db.commit()
    return to_out(preset)


def _owned_preset(db: Session, preset_id: int, user: User) -> StylePreset:
    preset = db.get(StylePreset, preset_id)
    if preset is None or preset.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Style preset not found (built-in presets cannot be changed)")
    return preset


@router.put("/{preset_id}", response_model=PresetOut)
def update_preset(preset_id: int, body: PresetIn, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    preset = _owned_preset(db, preset_id, user)
    for field, value in body.model_dump().items():
        setattr(preset, field, value)
    db.commit()
    return to_out(preset)


@router.delete("/{preset_id}", status_code=204)
def delete_preset(preset_id: int, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    db.delete(_owned_preset(db, preset_id, user))
    db.commit()


@router.post("/learn-voice", response_model=LearnVoiceOut, status_code=201)
def learn_voice(body: LearnVoiceIn, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    """Create a preset that reproduces a brand's voice, learned from its existing descriptions."""
    from app.routers.jobs import check_engine
    from app.services import brand_voice
    from app.services.llm import LLMError, provider_for_org

    samples = [s for s in body.samples if len(s.split()) >= 15]
    if body.catalog_id:
        get_owned(db, Catalog, body.catalog_id, user)
        rows = db.scalars(
            select(Product.original_description).where(
                Product.catalog_id == body.catalog_id, Product.original_description.is_not(None)
            ).limit(40)
        )
        samples += [s for s in rows if s and len(s.split()) >= 15]
    samples = samples[:20]
    if len(samples) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Provide at least 3 existing descriptions (15+ words each) to learn from")
    org = db.get(Organization, user.org_id)
    check_engine(body.engine, org)
    try:
        result = brand_voice.learn(samples, provider_for_org(body.engine, org))
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    profile = result.content
    preset = StylePreset(
        org_id=user.org_id, name=body.name, tone=profile.closest_tone, length=profile.typical_length,
        instructions=f"{profile.summary}\n\n{profile.instructions}".strip()[:4000],
        examples=[s.strip()[:1500] for s in samples[:3]],
    )
    db.add(preset)
    db.commit()
    return LearnVoiceOut(preset=to_out(preset), summary=profile.summary, samples_used=len(samples))


# ---------- Glossary ----------
glossary_router = APIRouter(prefix="/glossary", tags=["presets"])


@glossary_router.get("", response_model=list[GlossaryOut])
def list_terms(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(GlossaryTerm).where(GlossaryTerm.org_id == user.org_id).order_by(GlossaryTerm.term)).all()


@glossary_router.post("", response_model=GlossaryOut, status_code=201)
def add_term(body: GlossaryIn, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    term = body.term.strip()
    if db.scalar(select(GlossaryTerm.id).where(GlossaryTerm.org_id == user.org_id, GlossaryTerm.term == term)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This term is already in the glossary")
    row = GlossaryTerm(org_id=user.org_id, term=term, kind=body.kind,
                       replacement=(body.replacement or "").strip() or None if body.kind == "avoid" else None)
    db.add(row)
    db.commit()
    return row


@glossary_router.delete("/{term_id}", status_code=204)
def delete_term(term_id: int, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    db.delete(get_owned(db, GlossaryTerm, term_id, user))
    db.commit()