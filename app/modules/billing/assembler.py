"""
Transaction Assembler & Dependency Registry
Планирование изменений подписки с учетом зависимостей между сущностями.
"""
from typing import Dict, List, Optional, Set, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class DependencyType(str, Enum):
    """Типы зависимостей между сущностями."""
    HARD = "hard"  # Критическая зависимость, блокирует выполнение
    SOFT = "soft"  # Мягкая зависимость, предупреждение
    OPTIONAL = "optional"  # Опциональная зависимость


class TransactionState(str, Enum):
    """Состояния транзакции планирования."""
    PENDING = "pending"
    VALIDATING = "validating"
    READY = "ready"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class EntityNode:
    """Узел графа зависимостей сущности."""
    entity_id: str
    entity_type: str
    data: Dict[str, Any]
    dependencies: Set[str] = field(default_factory=set)
    dependents: Set[str] = field(default_factory=set)
    state: TransactionState = TransactionState.PENDING
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class TransactionPlan:
    """План транзакции изменений."""
    plan_id: str
    subscription_id: str
    nodes: Dict[str, EntityNode] = field(default_factory=dict)
    execution_order: List[str] = field(default_factory=list)
    state: TransactionState = TransactionState.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class DependencyRegistry:
    """
    Реестр зависимостей между сущностями биллинга.
    Отвечает за построение графа зависимостей и выявление циклов.
    """
    
    def __init__(self):
        self._dependencies: Dict[str, Dict[str, DependencyType]] = {}
        self._handlers: Dict[str, Dict[str, Callable]] = {}
        self._lock_dependencies: Dict[str, List[str]] = {}
    
    def register_dependency(
        self, 
        source_type: str, 
        target_type: str, 
        dep_type: DependencyType = DependencyType.HARD
    ):
        """Регистрация зависимости между типами сущностей."""
        if source_type not in self._dependencies:
            self._dependencies[source_type] = {}
        
        self._dependencies[source_type][target_type] = dep_type
        logger.info(f"Registered dependency: {source_type} -> {target_type} ({dep_type.value})")
    
    def register_handler(
        self, 
        entity_type: str, 
        operation: str, 
        handler: Callable
    ):
        """Регистрация обработчика операции для сущности."""
        if entity_type not in self._handlers:
            self._handlers[entity_type] = {}
        
        self._handlers[entity_type][operation] = handler
        logger.info(f"Registered handler: {entity_type}.{operation}")
    
    def get_handler(self, entity_type: str, operation: str) -> Optional[Callable]:
        """Получение обработчика для сущности и операции."""
        return self._handlers.get(entity_type, {}).get(operation)
    
    def get_dependencies(self, entity_type: str) -> Dict[str, DependencyType]:
        """Получение всех зависимостей для типа сущности."""
        return self._dependencies.get(entity_type, {})
    
    def validate_dependencies(self, entities: List[Dict[str, Any]]) -> List[str]:
        """
        Валидация зависимостей между сущностями.
        Возвращает список ошибок валидации.
        """
        errors = []
        entity_map = {f"{e['type']}:{e['id']}": e for e in entities}
        
        for entity in entities:
            entity_key = f"{entity['type']}:{entity['id']}"
            deps = self.get_dependencies(entity['type'])
            
            for dep_type, dep_rule in deps.items():
                if dep_type not in entity:
                    if dep_rule == DependencyType.HARD:
                        errors.append(f"Missing required dependency {dep_type} for {entity_key}")
                    continue
                
                dep_key = f"{dep_type}:{entity[dep_type]}"
                if dep_key not in entity_map and dep_rule == DependencyType.HARD:
                    errors.append(f"Dependency {dep_key} not found for {entity_key}")
        
        return errors
    
    def detect_cycles(self, entities: List[Dict[str, Any]]) -> bool:
        """
        Обнаружение циклических зависимостей в графе сущностей.
        Использует алгоритм DFS.
        """
        graph: Dict[str, List[str]] = {}
        
        for entity in entities:
            key = f"{entity['type']}:{entity['id']}"
            graph[key] = []
            
            deps = self.get_dependencies(entity['type'])
            for dep_type in deps.keys():
                if dep_type in entity:
                    dep_key = f"{dep_type}:{entity[dep_type]}"
                    graph[key].append(dep_key)
        
        visited = set()
        rec_stack = set()
        
        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            
            rec_stack.remove(node)
            return False
        
        for node in graph:
            if node not in visited:
                if has_cycle(node):
                    return True
        
        return False


class TransactionAssembler:
    """
    Сборщик транзакций.
    Планирует последовательность изменений с учетом зависимостей.
    """
    
    def __init__(self, registry: DependencyRegistry):
        self.registry = registry
        self._plans: Dict[str, TransactionPlan] = {}
    
    def create_plan(
        self, 
        subscription_id: str, 
        entities: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None
    ) -> TransactionPlan:
        """
        Создание плана транзакции для изменения подписки.
        """
        import uuid
        
        plan_id = str(uuid.uuid4())
        plan = TransactionPlan(
            plan_id=plan_id,
            subscription_id=subscription_id,
            metadata=metadata or {}
        )
        
        # Валидация зависимостей
        errors = self.registry.validate_dependencies(entities)
        if errors:
            plan.state = TransactionState.FAILED
            plan.nodes["error"] = EntityNode(
                entity_id="validation_error",
                entity_type="system",
                data={"errors": errors}
            )
            return plan
        
        # Проверка на циклы
        if self.registry.detect_cycles(entities):
            plan.state = TransactionState.FAILED
            plan.nodes["error"] = EntityNode(
                entity_id="cycle_detected",
                entity_type="system",
                data={"error": "Cyclic dependency detected"}
            )
            return plan
        
        # Построение графа узлов
        for entity in entities:
            key = f"{entity['type']}:{entity['id']}"
            node = EntityNode(
                entity_id=entity['id'],
                entity_type=entity['type'],
                data=entity
            )
            
            # Добавление зависимостей
            deps = self.registry.get_dependencies(entity['type'])
            for dep_type in deps.keys():
                if dep_type in entity:
                    dep_key = f"{dep_type}:{entity[dep_type]}"
                    node.dependencies.add(dep_key)
            
            plan.nodes[key] = node
        
        # Топологическая сортировка для определения порядка выполнения
        plan.execution_order = self._topological_sort(plan.nodes)
        plan.state = TransactionState.READY
        
        self._plans[plan_id] = plan
        logger.info(f"Created transaction plan {plan_id} with {len(plan.execution_order)} steps")
        
        return plan
    
    def _topological_sort(self, nodes: Dict[str, EntityNode]) -> List[str]:
        """
        Топологическая сортировка узлов графа.
        Возвращает порядок выполнения операций.
        """
        in_degree = {key: len(node.dependencies) for key, node in nodes.items()}
        queue = [key for key, degree in in_degree.items() if degree == 0]
        result = []
        
        while queue:
            node_key = queue.pop(0)
            result.append(node_key)
            
            node = nodes[node_key]
            for other_key, other_node in nodes.items():
                if node_key in other_node.dependencies:
                    in_degree[other_key] -= 1
                    if in_degree[other_key] == 0:
                        queue.append(other_key)
        
        if len(result) != len(nodes):
            raise ValueError("Graph has a cycle, topological sort impossible")
        
        return result
    
    def execute_plan(self, plan_id: str) -> bool:
        """
        Выполнение плана транзакции.
        Запускает обработчики для каждого узла в порядке очереди.
        """
        plan = self._plans.get(plan_id)
        if not plan:
            logger.error(f"Plan {plan_id} not found")
            return False
        
        if plan.state != TransactionState.READY:
            logger.warning(f"Plan {plan_id} is not ready: {plan.state}")
            return False
        
        plan.state = TransactionState.EXECUTING
        plan.started_at = datetime.utcnow()
        
        for node_key in plan.execution_order:
            node = plan.nodes.get(node_key)
            if not node:
                continue
            
            try:
                node.state = TransactionState.EXECUTING
                
                # Определение операции (по умолчанию 'update')
                operation = node.data.get('operation', 'update')
                
                # Получение и выполнение обработчика
                handler = self.registry.get_handler(node.entity_type, operation)
                if handler:
                    handler(node.data)
                
                node.state = TransactionState.COMPLETED
                node.updated_at = datetime.utcnow()
                
            except Exception as e:
                logger.error(f"Failed to execute node {node_key}: {str(e)}")
                node.state = TransactionState.FAILED
                node.error = str(e)
                plan.state = TransactionState.FAILED
                
                # Откат транзакции
                self._rollback_plan(plan)
                return False
        
        plan.state = TransactionState.COMPLETED
        plan.completed_at = datetime.utcnow()
        logger.info(f"Plan {plan_id} executed successfully")
        return True
    
    def _rollback_plan(self, plan: TransactionPlan):
        """Откат выполненной части плана."""
        logger.info(f"Rolling back plan {plan.plan_id}")
        
        for node_key in reversed(plan.execution_order):
            node = plan.nodes.get(node_key)
            if node and node.state == TransactionState.COMPLETED:
                try:
                    rollback_handler = self.registry.get_handler(node.entity_type, 'rollback')
                    if rollback_handler:
                        rollback_handler(node.data)
                    node.state = TransactionState.ROLLED_BACK
                except Exception as e:
                    logger.error(f"Rollback failed for {node_key}: {str(e)}")
        
        plan.state = TransactionState.ROLLED_BACK
        plan.completed_at = datetime.utcnow()
    
    def get_plan(self, plan_id: str) -> Optional[TransactionPlan]:
        """Получение плана по ID."""
        return self._plans.get(plan_id)


# Глобальный экземпляр реестра
default_registry = DependencyRegistry()

# Регистрация стандартных зависимостей биллинга
def init_default_dependencies():
    """Инициализация зависимостей по умолчанию."""
    registry = default_registry
    
    # Подписка зависит от пользователя
    registry.register_dependency('subscription', 'user_id', DependencyType.HARD)
    registry.register_dependency('subscription', 'plan_id', DependencyType.HARD)
    
    # Заказ зависит от подписки
    registry.register_dependency('order', 'subscription_id', DependencyType.HARD)
    
    # Платеж зависит от заказа
    registry.register_dependency('payment', 'order_id', DependencyType.HARD)
    
    # Чек зависит от платежа
    registry.register_dependency('fiscal_document', 'payment_id', DependencyType.HARD)
    
    logger.info("Default billing dependencies initialized")

init_default_dependencies()
