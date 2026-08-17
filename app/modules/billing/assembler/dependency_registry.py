"""
Dependency Registry для управления зависимостями между сущностями биллинга.
Определяет порядок применения изменений при обновлении подписок.
"""
from typing import Dict, List, Set, Any, Optional
from enum import Enum
from dataclasses import dataclass, field


class DependencyType(str, Enum):
    """Типы зависимостей между сущностями."""
    SUBSCRIPTION_PLAN = "subscription_plan"  # Подписка зависит от плана
    PLAN_PRICE = "plan_price"  # План зависит от цены
    PRICE_PRODUCT = "price_product"  # Цена зависит от продукта
    ENTITLEMENT_FEATURE = "entitlement_feature"  # Права зависят от фичи
    TRIAL_SUBSCRIPTION = "trial_subscription"  # Триал зависит от подписки


@dataclass
class DependencyNode:
    """Узел в графе зависимостей."""
    entity_type: str
    entity_id: str
    dependencies: Set[str] = field(default_factory=set)
    dependents: Set[str] = field(default_factory=set)
    
    def __hash__(self):
        return hash((self.entity_type, self.entity_id))
    
    def __eq__(self, other):
        if not isinstance(other, DependencyNode):
            return False
        return self.entity_type == other.entity_type and self.entity_id == other.entity_id


class DependencyRegistry:
    """
    Реестр зависимостей для определения порядка применения изменений.
    Используется TransactionAssembler для планирования транзакций.
    """
    
    def __init__(self):
        self._nodes: Dict[str, DependencyNode] = {}
        self._dependency_rules: Dict[DependencyType, tuple] = {
            DependencyType.SUBSCRIPTION_PLAN: ("subscription", "plan"),
            DependencyType.PLAN_PRICE: ("plan", "price"),
            DependencyType.PRICE_PRODUCT: ("price", "product"),
            DependencyType.ENTITLEMENT_FEATURE: ("entitlement", "feature"),
            DependencyType.TRIAL_SUBSCRIPTION: ("trial", "subscription"),
        }
    
    def _make_key(self, entity_type: str, entity_id: str) -> str:
        """Создает уникальный ключ для сущности."""
        return f"{entity_type}:{entity_id}"
    
    def register_dependency(
        self,
        dependent_type: str,
        dependent_id: str,
        dependency_type: str,
        dependency_id: str,
        dep_rule: DependencyType
    ) -> None:
        """
        Регистрирует зависимость между сущностями.
        
        Args:
            dependent_type: Тип зависимой сущности
            dependent_id: ID зависимой сущности
            dependency_type: Тип сущности, от которой зависит
            dependency_id: ID сущности, от которой зависит
            dep_rule: Правило зависимости
        """
        expected_dependent, expected_dependency = self._dependency_rules[dep_rule]
        
        if dependent_type != expected_dependent:
            raise ValueError(
                f"Expected dependent type '{expected_dependent}', got '{dependent_type}'"
            )
        if dependency_type != expected_dependency:
            raise ValueError(
                f"Expected dependency type '{expected_dependency}', got '{dependency_type}'"
            )
        
        dependent_key = self._make_key(dependent_type, dependent_id)
        dependency_key = self._make_key(dependency_type, dependency_id)
        
        # Создаем узлы если их нет
        if dependent_key not in self._nodes:
            self._nodes[dependent_key] = DependencyNode(
                entity_type=dependent_type,
                entity_id=dependent_id
            )
        
        if dependency_key not in self._nodes:
            self._nodes[dependency_key] = DependencyNode(
                entity_type=dependency_type,
                entity_id=dependency_id
            )
        
        # Добавляем связи
        self._nodes[dependent_key].dependencies.add(dependency_key)
        self._nodes[dependency_key].dependents.add(dependent_key)
    
    def get_execution_order(self, changed_entities: List[tuple]) -> List[tuple]:
        """
        Определяет порядок выполнения изменений на основе зависимостей.
        Использует топологическую сортировку.
        
        Args:
            changed_entities: Список кортежей (entity_type, entity_id, operation)
            
        Returns:
            Отсортированный список кортежей для выполнения
        """
        # Строим подграф из измененных сущностей
        subgraph_keys: Set[str] = set()
        for entity_type, entity_id, _ in changed_entities:
            key = self._make_key(entity_type, entity_id)
            subgraph_keys.add(key)
        
        # Добавляем все зависимости рекурсивно
        keys_to_process = list(subgraph_keys)
        while keys_to_process:
            key = keys_to_process.pop()
            if key in self._nodes:
                for dep_key in self._nodes[key].dependencies:
                    if dep_key not in subgraph_keys:
                        subgraph_keys.add(dep_key)
                        keys_to_process.append(dep_key)
        
        # Топологическая сортировка (алгоритм Кана)
        in_degree: Dict[str, int] = {key: 0 for key in subgraph_keys}
        
        for key in subgraph_keys:
            if key in self._nodes:
                for dep_key in self._nodes[key].dependencies:
                    if dep_key in subgraph_keys:
                        in_degree[key] = in_degree.get(key, 0) + 1
        
        # Находим узлы без входящих зависимостей
        queue = [key for key, degree in in_degree.items() if degree == 0]
        result: List[str] = []
        
        while queue:
            current = queue.pop(0)
            result.append(current)
            
            if current in self._nodes:
                for dependent_key in self._nodes[current].dependents:
                    if dependent_key in in_degree:
                        in_degree[dependent_key] -= 1
                        if in_degree[dependent_key] == 0:
                            queue.append(dependent_key)
        
        # Проверяем на циклы
        if len(result) != len(subgraph_keys):
            raise ValueError("Обнаружен цикл в зависимостях!")
        
        # Преобразуем обратно в кортежи сущностей
        entity_map = {
            self._make_key(et, eid): (et, eid, op)
            for et, eid, op in changed_entities
        }
        
        execution_order = []
        for key in result:
            if key in entity_map:
                execution_order.append(entity_map[key])
            elif key in self._nodes:
                # Для зависимостей, которые не были явно изменены
                node = self._nodes[key]
                execution_order.append((node.entity_type, node.entity_id, "read"))
        
        return execution_order
    
    def get_dependents(self, entity_type: str, entity_id: str) -> List[tuple]:
        """
        Возвращает все сущности, которые зависят от указанной.
        
        Args:
            entity_type: Тип сущности
            entity_id: ID сущности
            
        Returns:
            Список кортежей (entity_type, entity_id) зависимых сущностей
        """
        key = self._make_key(entity_type, entity_id)
        if key not in self._nodes:
            return []
        
        result = []
        visited = set()
        stack = [key]
        
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            
            if current in self._nodes:
                for dependent_key in self._nodes[current].dependents:
                    if dependent_key not in visited:
                        stack.append(dependent_key)
                        dep_node = self._nodes[dependent_key]
                        result.append((dep_node.entity_type, dep_node.entity_id))
        
        return result
    
    def clear(self) -> None:
        """Очищает реестр зависимостей."""
        self._nodes.clear()
