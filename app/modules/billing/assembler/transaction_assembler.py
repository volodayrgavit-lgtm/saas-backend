"""
TransactionAssembler - сборщик транзакций для планирования изменений в биллинге.
Использует DependencyRegistry для определения правильного порядка применения изменений.
"""
from typing import Dict, List, Any, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging

from .dependency_registry import DependencyRegistry, DependencyType


logger = logging.getLogger(__name__)


class TransactionOperation(str, Enum):
    """Операции транзакции."""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"
    RENEW = "renew"
    CANCEL = "cancel"


@dataclass
class TransactionStep:
    """Шаг транзакции."""
    entity_type: str
    entity_id: str
    operation: TransactionOperation
    data: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    priority: int = 0
    retry_count: int = 0
    max_retries: int = 3
    
    def __hash__(self):
        return hash((self.entity_type, self.entity_id, self.operation))


@dataclass
class PlannedTransaction:
    """Запланированная транзакция."""
    transaction_id: str
    steps: List[TransactionStep] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "pending"  # pending, executing, completed, failed
    error_message: Optional[str] = None
    
    def add_step(self, step: TransactionStep) -> None:
        """Добавляет шаг в транзакцию."""
        self.steps.append(step)
    
    def mark_completed(self) -> None:
        """Отмечает транзакцию как выполненную."""
        self.status = "completed"
    
    def mark_failed(self, error: str) -> None:
        """Отмечает транзакцию как неудачную."""
        self.status = "failed"
        self.error_message = error


class TransactionAssembler:
    """
    Сборщик транзакций для планирования и выполнения изменений в биллинге.
    Использует DependencyRegistry для определения порядка выполнения шагов.
    """
    
    def __init__(self, dependency_registry: Optional[DependencyRegistry] = None):
        self.dependency_registry = dependency_registry or DependencyRegistry()
        self._pending_transactions: Dict[str, PlannedTransaction] = {}
        self._execution_handlers: Dict[str, Callable] = {}
        self._transaction_counter = 0
    
    def register_handler(
        self,
        entity_type: str,
        handler: Callable[[str, str, TransactionOperation, Dict[str, Any]], Awaitable[bool]]
    ) -> None:
        """
        Регистрирует обработчик для типа сущности.
        
        Args:
            entity_type: Тип сущности (subscription, plan, order, etc.)
            handler: Асинхронная функция-обработчик
        """
        self._execution_handlers[entity_type] = handler
        logger.info(f"Registered handler for entity type: {entity_type}")
    
    def _generate_transaction_id(self) -> str:
        """Генерирует уникальный ID транзакции."""
        self._transaction_counter += 1
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return f"txn_{timestamp}_{self._transaction_counter:06d}"
    
    def create_transaction(
        self,
        changes: List[Dict[str, Any]]
    ) -> PlannedTransaction:
        """
        Создает запланированную транзакцию на основе списка изменений.
        
        Args:
            changes: Список изменений, каждое изменение содержит:
                - entity_type: тип сущности
                - entity_id: ID сущности
                - operation: операция
                - data: данные для операции
                - dependencies: зависимости (опционально)
                
        Returns:
            PlannedTransaction с упорядоченными шагами
        """
        transaction_id = self._generate_transaction_id()
        transaction = PlannedTransaction(transaction_id=transaction_id)
        
        # Преобразуем изменения в кортежи для сортировки
        change_tuples = []
        change_map = {}
        
        for change in changes:
            entity_type = change["entity_type"]
            entity_id = change["entity_id"]
            operation = change["operation"]
            data = change.get("data", {})
            deps = change.get("dependencies", [])
            
            change_tuple = (entity_type, entity_id, operation)
            change_tuples.append(change_tuple)
            change_map[change_tuple] = {
                "data": data,
                "dependencies": deps
            }
            
            # Регистрируем зависимости в реестре
            # Формат зависимости: "dependent_type:dependent_id" зависит от "dependency_type:dependency_id"
            for dep in deps:
                dep_type, dep_id = dep.split(":")
                try:
                    # subscription зависит от plan -> SUBSCRIPTION_PLAN
                    if entity_type == "subscription" and dep_type == "plan":
                        dep_rule = DependencyType.SUBSCRIPTION_PLAN
                    elif entity_type == "plan" and dep_type == "price":
                        dep_rule = DependencyType.PLAN_PRICE
                    elif entity_type == "price" and dep_type == "product":
                        dep_rule = DependencyType.PRICE_PRODUCT
                    elif entity_type == "entitlement" and dep_type == "feature":
                        dep_rule = DependencyType.ENTITLEMENT_FEATURE
                    elif entity_type == "trial" and dep_type == "subscription":
                        dep_rule = DependencyType.TRIAL_SUBSCRIPTION
                    else:
                        # Пропускаем неизвестные правила
                        continue
                    
                    self.dependency_registry.register_dependency(
                        entity_type, entity_id,
                        dep_type, dep_id,
                        dep_rule
                    )
                except (ValueError, KeyError):
                    # Если правило не найдено, пропускаем
                    pass
        
        # Получаем порядок выполнения
        try:
            execution_order = self.dependency_registry.get_execution_order(change_tuples)
        except ValueError as e:
            logger.error(f"Error determining execution order: {e}")
            transaction.mark_failed(str(e))
            return transaction
        
        # Создаем шаги транзакции в правильном порядке
        for idx, (entity_type, entity_id, operation) in enumerate(execution_order):
            if (entity_type, entity_id, operation) in change_map:
                change_info = change_map[(entity_type, entity_id, operation)]
                step = TransactionStep(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    operation=TransactionOperation(operation),
                    data=change_info["data"],
                    dependencies=change_info["dependencies"],
                    priority=idx  # Приоритет на основе позиции в порядке выполнения
                )
                transaction.add_step(step)
        
        self._pending_transactions[transaction_id] = transaction
        logger.info(f"Created transaction {transaction_id} with {len(transaction.steps)} steps")
        
        return transaction
    
    async def execute_transaction(
        self,
        transaction_id: str
    ) -> bool:
        """
        Выполняет запланированную транзакцию.
        
        Args:
            transaction_id: ID транзакции
            
        Returns:
            True если транзакция выполнена успешно, False иначе
        """
        if transaction_id not in self._pending_transactions:
            logger.error(f"Transaction {transaction_id} not found")
            return False
        
        transaction = self._pending_transactions[transaction_id]
        if transaction.status != "pending":
            logger.warning(f"Transaction {transaction_id} is not pending (status: {transaction.status})")
            return False
        
        transaction.status = "executing"
        logger.info(f"Executing transaction {transaction_id}")
        
        for step in transaction.steps:
            if step.entity_type not in self._execution_handlers:
                error_msg = f"No handler registered for entity type: {step.entity_type}"
                logger.error(error_msg)
                transaction.mark_failed(error_msg)
                return False
            
            handler = self._execution_handlers[step.entity_type]
            
            # Попытка выполнения с повторами
            success = False
            for attempt in range(step.max_retries):
                try:
                    step.retry_count = attempt + 1
                    result = await handler(
                        step.entity_id,
                        step.operation.value,
                        step.data
                    )
                    if result:
                        success = True
                        break
                    else:
                        logger.warning(
                            f"Step failed (attempt {attempt + 1}/{step.max_retries}): "
                            f"{step.entity_type}:{step.entity_id}"
                        )
                except Exception as e:
                    logger.error(
                        f"Step error (attempt {attempt + 1}/{step.max_retries}): "
                        f"{step.entity_type}:{step.entity_id} - {e}"
                    )
            
            if not success:
                error_msg = f"Failed to execute step {step.entity_type}:{step.entity_id}"
                transaction.mark_failed(error_msg)
                logger.error(error_msg)
                return False
        
        transaction.mark_completed()
        logger.info(f"Transaction {transaction_id} completed successfully")
        return True
    
    def get_transaction(self, transaction_id: str) -> Optional[PlannedTransaction]:
        """Возвращает транзакцию по ID."""
        return self._pending_transactions.get(transaction_id)
    
    def cancel_transaction(self, transaction_id: str) -> bool:
        """Отменяет_pending транзакцию."""
        if transaction_id not in self._pending_transactions:
            return False
        
        transaction = self._pending_transactions[transaction_id]
        if transaction.status != "pending":
            return False
        
        transaction.status = "cancelled"
        logger.info(f"Cancelled transaction {transaction_id}")
        return True
    
    def list_pending_transactions(self) -> List[PlannedTransaction]:
        """Возвращает список всех pending транзакций."""
        return [
            txn for txn in self._pending_transactions.values()
            if txn.status == "pending"
        ]
    
    def clear_completed(self, older_than: Optional[datetime] = None) -> int:
        """
        Очищает завершенные транзакции.
        
        Args:
            older_than: Удалять только транзакции старше указанной даты
            
        Returns:
            Количество удаленных транзакций
        """
        to_remove = []
        for txn_id, txn in self._pending_transactions.items():
            if txn.status in ("completed", "failed", "cancelled"):
                if older_than is None or txn.created_at < older_than:
                    to_remove.append(txn_id)
        
        for txn_id in to_remove:
            del self._pending_transactions[txn_id]
        
        logger.info(f"Cleared {len(to_remove)} completed transactions")
        return len(to_remove)
