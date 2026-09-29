"""资料可见边界：按角色与学生同意范围过滤材料。"""
from __future__ import annotations

from .models import ActorRole, Case, Material


def _full(material: Material) -> dict:
    return {
        "material_id": material.material_id,
        "category": material.category.value,
        "title": material.title,
        "content": material.content,
        "source_office": material.source_office,
        "uploaded_at": material.uploaded_at.isoformat(),
    }


def _redacted(material: Material) -> dict:
    """协调员视图：只保留类别与来源，不泄露标题与内容。"""
    return {
        "material_id": material.material_id,
        "category": material.category.value,
        "source_office": material.source_office,
        "uploaded_at": material.uploaded_at.isoformat(),
    }


def visible_materials(case: Case, *, role: ActorRole, office: str | None = None) -> list[dict]:
    """按角色与同意范围返回可见材料。

    - 学生可见自己的全部材料；
    - 支持专员仅可见学生同意给其机构的类别；
    - 项目协调员只见类别与来源等元数据，不见敏感内容。
    """
    if role is ActorRole.STUDENT:
        return [_full(material) for material in case.materials]
    if role is ActorRole.COORDINATOR:
        return [_redacted(material) for material in case.materials]
    if role is ActorRole.HANDLER and office is not None:
        return [
            _full(material)
            for material in case.materials
            if case.consent.allows_material(material.category, office)
        ]
    return []
