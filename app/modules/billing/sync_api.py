"""
Sync/Share REST API для синхронизации состояния
Механизм обмена данными между сервисами биллинга.
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Body
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["billing-sync"])


# =====================
# Schemas
# =====================

class SyncVersion(BaseModel):
    """Версия синхронизации."""
    entity_type: str
    entity_id: str
    version: int
    updated_at: datetime


class SyncTransaction(BaseModel):
    """Транзакция синхронизации."""
    id: str
    entity_type: str
    entity_id: str
    action: str  # create, update, delete
    payload: Dict[str, Any]
    version: int
    created_at: datetime
    processed_at: Optional[datetime] = None
    status: str = "pending"  # pending, processed, failed


class SyncRequest(BaseModel):
    """Запрос на синхронизацию."""
    entity_type: str
    entity_id: Optional[str] = None
    from_version: int = 0
    limit: int = Field(default=100, ge=1, le=1000)


class SyncResponse(BaseModel):
    """Ответ синхронизации."""
    transactions: List[SyncTransaction]
    current_version: int
    has_more: bool
    synced_at: datetime


class ShareDataRequest(BaseModel):
    """Запрос на обмен данными."""
    source_service: str
    target_services: List[str]
    entity_type: str
    entity_id: str
    data: Dict[str, Any]
    sync_mode: str = "full"  # full, incremental, delta


class ShareDataResponse(BaseModel):
    """Ответ обмена данными."""
    success: bool
    delivered_to: List[str]
    failed_to: List[str]
    message: Optional[str] = None


class EntityState(BaseModel):
    """Состояние сущности."""
    entity_type: str
    entity_id: str
    data: Dict[str, Any]
    version: int
    last_synced_at: Optional[datetime] = None
    checksum: str


class ChecksumRequest(BaseModel):
    """Запрос контрольной суммы."""
    entities: List[Dict[str, str]]  # [{entity_type, entity_id}, ...]


class ChecksumResponse(BaseModel):
    """Ответ контрольной суммы."""
    checksums: Dict[str, str]  # {entity_key: checksum}
    mismatches: List[Dict[str, Any]]  # Расхождения


# =====================
# Mock Storage (заменить на реальную БД)
# =====================

class SyncStorage:
    """Хранилище транзакций синхронизации."""
    
    def __init__(self):
        self._transactions: Dict[str, SyncTransaction] = {}
        self._versions: Dict[str, int] = {}  # {entity_key: version}
        self._entity_states: Dict[str, EntityState] = {}
    
    def add_transaction(self, transaction: SyncTransaction):
        """Добавление транзакции."""
        self._transactions[transaction.id] = transaction
        
        # Обновление версии
        key = f"{transaction.entity_type}:{transaction.entity_id}"
        current_version = self._versions.get(key, 0)
        if transaction.version > current_version:
            self._versions[key] = transaction.version
    
    def get_transactions(
        self,
        entity_type: str,
        entity_id: Optional[str] = None,
        from_version: int = 0,
        limit: int = 100
    ) -> List[SyncTransaction]:
        """Получение транзакций для синхронизации."""
        result = []
        
        for tx in self._transactions.values():
            if tx.entity_type != entity_type:
                continue
            
            if entity_id and tx.entity_id != entity_id:
                continue
            
            if tx.version <= from_version:
                continue
            
            if tx.status == "processed":
                continue
            
            result.append(tx)
            
            if len(result) >= limit:
                break
        
        return sorted(result, key=lambda x: x.version)
    
    def get_current_version(self, entity_type: str, entity_id: str) -> int:
        """Получение текущей версии сущности."""
        key = f"{entity_type}:{entity_id}"
        return self._versions.get(key, 0)
    
    def update_entity_state(self, state: EntityState):
        """Обновление состояния сущности."""
        key = f"{state.entity_type}:{state.entity_id}"
        self._entity_states[key] = state
    
    def get_entity_state(self, entity_type: str, entity_id: str) -> Optional[EntityState]:
        """Получение состояния сущности."""
        key = f"{entity_type}:{entity_id}"
        return self._entity_states.get(key)
    
    def calculate_checksum(self, entity_type: str, entity_id: str) -> str:
        """Вычисление контрольной суммы сущности."""
        import hashlib
        state = self.get_entity_state(entity_type, entity_id)
        if not state:
            return ""
        
        data_str = f"{entity_type}:{entity_id}:{state.version}:{str(state.data)}"
        return hashlib.sha256(data_str.encode()).hexdigest()[:16]


# Глобальное хранилище
sync_storage = SyncStorage()


# =====================
# API Endpoints
# =====================

@router.post("/transactions", response_model=SyncTransaction, status_code=201)
async def create_sync_transaction(transaction: SyncTransaction):
    """
    Создание транзакции синхронизации.
    Вызывается при изменении сущности биллинга.
    """
    # Валидация версии
    current_version = sync_storage.get_current_version(
        transaction.entity_type,
        transaction.entity_id
    )
    
    if transaction.version <= current_version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict: expected > {current_version}, got {transaction.version}"
        )
    
    transaction.status = "pending"
    transaction.created_at = datetime.utcnow()
    sync_storage.add_transaction(transaction)
    
    logger.info(f"Created sync transaction {transaction.id} for {transaction.entity_type}:{transaction.entity_id}")
    return transaction


@router.post("/sync", response_model=SyncResponse)
async def sync_data(request: SyncRequest):
    """
    Синхронизация данных.
    Возвращает список изменений с указанной версии.
    """
    transactions = sync_storage.get_transactions(
        entity_type=request.entity_type,
        entity_id=request.entity_id,
        from_version=request.from_version,
        limit=request.limit
    )
    
    # Определение текущей версии
    if request.entity_id:
        current_version = sync_storage.get_current_version(
            request.entity_type,
            request.entity_id
        )
    else:
        # Максимальная версия для типа сущности
        current_version = max(
            (v for k, v in sync_storage._versions.items() if k.startswith(f"{request.entity_type}:")),
            default=0
        )
    
    has_more = len(transactions) >= request.limit
    
    return SyncResponse(
        transactions=transactions,
        current_version=current_version,
        has_more=has_more,
        synced_at=datetime.utcnow()
    )


@router.post("/share", response_model=ShareDataResponse)
async def share_data(request: ShareDataRequest):
    """
    Обмен данными между сервисами.
    Рассылает данные указанным целевым сервисам.
    """
    delivered_to = []
    failed_to = []
    
    # В реальном проекте здесь будет вызов других сервисов
    for target_service in request.target_services:
        try:
            # Имитация отправки данных
            # В реальности: HTTP/gRPC вызов к target_service
            logger.info(f"Sharing {request.entity_type}:{request.entity_id} to {target_service}")
            
            # Создание транзакции синхронизации
            transaction = SyncTransaction(
                id=f"share_{target_service}_{datetime.utcnow().timestamp()}",
                entity_type=request.entity_type,
                entity_id=request.entity_id,
                action="update",
                payload={
                    "source": request.source_service,
                    "data": request.data,
                    "sync_mode": request.sync_mode
                },
                version=sync_storage.get_current_version(request.entity_type, request.entity_id) + 1,
                created_at=datetime.utcnow()
            )
            sync_storage.add_transaction(transaction)
            
            delivered_to.append(target_service)
            
        except Exception as e:
            logger.error(f"Failed to share data with {target_service}: {str(e)}")
            failed_to.append(target_service)
    
    success = len(failed_to) == 0
    
    return ShareDataResponse(
        success=success,
        delivered_to=delivered_to,
        failed_to=failed_to,
        message="Data shared successfully" if success else f"Partially failed: {len(failed_to)} services"
    )


@router.get("/state/{entity_type}/{entity_id}", response_model=EntityState)
async def get_entity_state(entity_type: str, entity_id: str):
    """
    Получение текущего состояния сущности.
    """
    state = sync_storage.get_entity_state(entity_type, entity_id)
    
    if not state:
        raise HTTPException(status_code=404, detail="Entity state not found")
    
    return state


@router.put("/state", response_model=EntityState)
async def update_entity_state(state: EntityState):
    """
    Обновление состояния сущности.
    """
    state.last_synced_at = datetime.utcnow()
    
    # Вычисление checksum
    import hashlib
    data_str = f"{state.entity_type}:{state.entity_id}:{state.version}:{str(state.data)}"
    state.checksum = hashlib.sha256(data_str.encode()).hexdigest()[:16]
    
    sync_storage.update_entity_state(state)
    logger.info(f"Updated entity state for {state.entity_type}:{state.entity_id}")
    
    return state


@router.post("/checksums", response_model=ChecksumResponse)
async def verify_checksums(request: ChecksumRequest):
    """
    Проверка контрольных сумм сущностей.
    Используется для выявления расхождений между сервисами.
    """
    checksums = {}
    mismatches = []
    
    for entity in request.entities:
        entity_type = entity.get("entity_type")
        entity_id = entity.get("entity_id")
        remote_checksum = entity.get("checksum")
        
        if not entity_type or not entity_id:
            continue
        
        local_checksum = sync_storage.calculate_checksum(entity_type, entity_id)
        key = f"{entity_type}:{entity_id}"
        checksums[key] = local_checksum
        
        if remote_checksum and local_checksum != remote_checksum:
            mismatches.append({
                "entity_type": entity_type,
                "entity_id": entity_id,
                "local_checksum": local_checksum,
                "remote_checksum": remote_checksum
            })
    
    return ChecksumResponse(
        checksums=checksums,
        mismatches=mismatches
    )


@router.get("/stats")
async def get_sync_stats():
    """
    Получение статистики синхронизации.
    """
    total_transactions = len(sync_storage._transactions)
    pending_transactions = sum(1 for t in sync_storage._transactions.values() if t.status == "pending")
    processed_transactions = sum(1 for t in sync_storage._transactions.values() if t.status == "processed")
    failed_transactions = sum(1 for t in sync_storage._transactions.values() if t.status == "failed")
    
    return {
        "total_transactions": total_transactions,
        "pending_transactions": pending_transactions,
        "processed_transactions": processed_transactions,
        "failed_transactions": failed_transactions,
        "tracked_entities": len(sync_storage._entity_states),
        "max_version": max(sync_storage._versions.values(), default=0)
    }


# =====================
# Utility Functions
# =====================

def emit_sync_event(
    entity_type: str,
    entity_id: str,
    action: str,
    payload: Dict[str, Any],
    version: int
) -> SyncTransaction:
    """
    Создание и сохранение транзакции синхронизации.
    Вызывается при изменениях сущностей.
    """
    import uuid
    
    transaction = SyncTransaction(
        id=str(uuid.uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        payload=payload,
        version=version,
        created_at=datetime.utcnow()
    )
    
    sync_storage.add_transaction(transaction)
    logger.info(f"Emitted sync event: {action} {entity_type}:{entity_id} (v{version})")
    
    return transaction
