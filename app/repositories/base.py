"""
app/repositories/base.py — Generic SQLAlchemy Repository
=========================================================
Implements the Repository pattern: abstract CRUD operations
that work for any SQLAlchemy model.
"""

from typing import Generic, TypeVar, Type, Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, func, inspect

ModelType = TypeVar("ModelType")


class BaseRepository(Generic[ModelType]):
    def __init__(self, model: Type[ModelType], session: Session):
        self.model = model
        self.session = session

    def get(self, id: Any) -> Optional[ModelType]:
        return self.session.get(self.model, id)

    def list(
        self,
        skip: int = 0,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        desc: bool = False,
    ) -> List[ModelType]:
        stmt = select(self.model)
        if filters:
            for col, val in filters.items():
                if hasattr(self.model, col):
                    column = getattr(self.model, col)
                    if val is not None:
                        stmt = stmt.where(column == val)
        if order_by and hasattr(self.model, order_by):
            col = getattr(self.model, order_by)
            stmt = stmt.order_by(col.desc() if desc else col)
        stmt = stmt.offset(skip).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def create(self, **kwargs) -> ModelType:
        instance = self.model(**kwargs)
        self.session.add(instance)
        self.session.flush()
        self.session.refresh(instance)
        return instance

    def update(self, id: Any, **kwargs) -> Optional[ModelType]:
        instance = self.get(id)
        if not instance:
            return None
        for key, value in kwargs.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        self.session.flush()
        self.session.refresh(instance)
        return instance

    def delete(self, id: Any) -> bool:
        instance = self.get(id)
        if not instance:
            return False
        self.session.delete(instance)
        self.session.flush()
        return True

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        stmt = select(func.count()).select_from(self.model)
        if filters:
            for col, val in filters.items():
                if hasattr(self.model, col):
                    column = getattr(self.model, col)
                    if val is not None:
                        stmt = stmt.where(column == val)
        result = self.session.execute(stmt).scalar()
        return result or 0

    def bulk_create(self, items: List[Dict[str, Any]]) -> List[ModelType]:
        instances = [self.model(**item) for item in items]
        self.session.add_all(instances)
        self.session.flush()
        for inst in instances:
            self.session.refresh(inst)
        return instances

    def upsert(self, unique_col: str, unique_val: Any, **kwargs) -> ModelType:
        """Find by unique column or create."""
        column = getattr(self.model, unique_col)
        stmt = select(self.model).where(column == unique_val)
        instance = self.session.execute(stmt).scalar_one_or_none()
        if instance:
            for key, value in kwargs.items():
                if hasattr(instance, key):
                    setattr(instance, key, value)
            self.session.flush()
            self.session.refresh(instance)
            return instance
        return self.create(**kwargs)
