"""
Tests for DependencyRegistry and TransactionAssembler.
"""
import pytest
from datetime import datetime
import asyncio

from app.modules.billing.assembler.dependency_registry import (
    DependencyRegistry,
    DependencyType,
    DependencyNode
)
from app.modules.billing.assembler.transaction_assembler import (
    TransactionAssembler,
    TransactionStep,
    PlannedTransaction,
    TransactionOperation
)


class TestDependencyRegistry:
    """Тесты для DependencyRegistry."""
    
    def test_register_dependency_success(self):
        """Тест успешной регистрации зависимости."""
        registry = DependencyRegistry()
        
        # Регистрируем зависимость: подписка зависит от плана
        registry.register_dependency(
            dependent_type="subscription",
            dependent_id="sub_123",
            dependency_type="plan",
            dependency_id="plan_456",
            dep_rule=DependencyType.SUBSCRIPTION_PLAN
        )
        
        # Проверяем что узлы созданы
        assert "subscription:sub_123" in registry._nodes
        assert "plan:plan_456" in registry._nodes
        
        # Проверяем связи
        sub_node = registry._nodes["subscription:sub_123"]
        plan_node = registry._nodes["plan:plan_456"]
        
        assert "plan:plan_456" in sub_node.dependencies
        assert "subscription:sub_123" in plan_node.dependents
    
    def test_register_dependency_invalid_type(self):
        """Тест регистрации с неверным типом."""
        registry = DependencyRegistry()
        
        with pytest.raises(ValueError, match="Expected dependent type"):
            registry.register_dependency(
                dependent_type="invalid",  # Неверный тип
                dependent_id="sub_123",
                dependency_type="plan",
                dependency_id="plan_456",
                dep_rule=DependencyType.SUBSCRIPTION_PLAN
            )
    
    def test_get_execution_order_simple(self):
        """Тест простого порядка выполнения."""
        registry = DependencyRegistry()
        
        # План -> Подписка
        registry.register_dependency(
            "subscription", "sub_1",
            "plan", "plan_1",
            DependencyType.SUBSCRIPTION_PLAN
        )
        
        changes = [
            ("plan", "plan_1", "update"),
            ("subscription", "sub_1", "update")
        ]
        
        order = registry.get_execution_order(changes)
        
        # План должен быть перед подпиской
        plan_idx = next(i for i, (et, _, _) in enumerate(order) if et == "plan")
        sub_idx = next(i for i, (et, _, _) in enumerate(order) if et == "subscription")
        
        assert plan_idx < sub_idx
    
    def test_get_execution_order_complex(self):
        """Тест сложного порядка выполнения с цепочкой зависимостей."""
        registry = DependencyRegistry()
        
        # Product -> Price -> Plan -> Subscription
        registry.register_dependency(
            "price", "price_1",
            "product", "prod_1",
            DependencyType.PRICE_PRODUCT
        )
        registry.register_dependency(
            "plan", "plan_1",
            "price", "price_1",
            DependencyType.PLAN_PRICE
        )
        registry.register_dependency(
            "subscription", "sub_1",
            "plan", "plan_1",
            DependencyType.SUBSCRIPTION_PLAN
        )
        
        changes = [
            ("subscription", "sub_1", "update"),
            ("product", "prod_1", "update"),
            ("plan", "plan_1", "update"),
            ("price", "price_1", "update")
        ]
        
        order = registry.get_execution_order(changes)
        
        # Проверяем порядок: product -> price -> plan -> subscription
        expected_order = ["product", "price", "plan", "subscription"]
        actual_order = [et for et, _, _ in order]
        
        assert actual_order == expected_order
    
    def test_get_dependents(self):
        """Тест получения зависимых сущностей."""
        registry = DependencyRegistry()
        
        # Создаем дерево: product -> price -> plan -> subscription
        registry.register_dependency(
            "price", "price_1", "product", "prod_1", DependencyType.PRICE_PRODUCT
        )
        registry.register_dependency(
            "plan", "plan_1", "price", "price_1", DependencyType.PLAN_PRICE
        )
        registry.register_dependency(
            "subscription", "sub_1", "plan", "plan_1", DependencyType.SUBSCRIPTION_PLAN
        )
        
        # Получаем всех зависимых от product
        dependents = registry.get_dependents("product", "prod_1")
        
        dependent_types = [dt for dt, _ in dependents]
        assert "price" in dependent_types
        assert "plan" in dependent_types
        assert "subscription" in dependent_types
    
    def test_cycle_detection(self):
        """Тест обнаружения цикла в зависимостях."""
        registry = DependencyRegistry()
        
        # Создаем искусственный цикл через прямой доступ к узлам
        key_a = "entity:a"
        key_b = "entity:b"
        
        registry._nodes[key_a] = DependencyNode("entity", "a")
        registry._nodes[key_b] = DependencyNode("entity", "b")
        
        # Создаем цикл: a -> b -> a
        registry._nodes[key_a].dependencies.add(key_b)
        registry._nodes[key_b].dependencies.add(key_a)
        registry._nodes[key_a].dependents.add(key_b)
        registry._nodes[key_b].dependents.add(key_a)
        
        changes = [("entity", "a", "update"), ("entity", "b", "update")]
        
        with pytest.raises(ValueError, match="цикл"):
            registry.get_execution_order(changes)
    
    def test_clear(self):
        """Тест очистки реестра."""
        registry = DependencyRegistry()
        
        registry.register_dependency(
            "subscription", "sub_1",
            "plan", "plan_1",
            DependencyType.SUBSCRIPTION_PLAN
        )
        
        assert len(registry._nodes) > 0
        
        registry.clear()
        
        assert len(registry._nodes) == 0


class TestTransactionAssembler:
    """Тесты для TransactionAssembler."""
    
    @pytest.fixture
    def assembler(self):
        """Фикстура с TransactionAssembler."""
        return TransactionAssembler()
    
    def test_create_transaction_simple(self, assembler):
        """Тест создания простой транзакции."""
        changes = [
            {
                "entity_type": "subscription",
                "entity_id": "sub_123",
                "operation": "update",
                "data": {"status": "active"},
                "dependencies": []
            }
        ]
        
        transaction = assembler.create_transaction(changes)
        
        assert transaction.transaction_id.startswith("txn_")
        assert transaction.status == "pending"
        assert len(transaction.steps) == 1
        assert transaction.steps[0].entity_type == "subscription"
        assert transaction.steps[0].entity_id == "sub_123"
    
    def test_create_transaction_with_dependencies(self, assembler):
        """Тест создания транзакции с зависимостями."""
        changes = [
            {
                "entity_type": "subscription",
                "entity_id": "sub_1",
                "operation": "activate",
                "data": {},
                "dependencies": ["plan:plan_1"]
            },
            {
                "entity_type": "plan",
                "entity_id": "plan_1",
                "operation": "update",
                "data": {"name": "New Plan"},
                "dependencies": []
            }
        ]
        
        transaction = assembler.create_transaction(changes)
        
        assert len(transaction.steps) >= 2
        
        # Plan должен быть перед subscription
        plan_step = next((s for s in transaction.steps if s.entity_type == "plan"), None)
        sub_step = next((s for s in transaction.steps if s.entity_type == "subscription"), None)
        
        assert plan_step is not None
        assert sub_step is not None
        assert transaction.steps.index(plan_step) < transaction.steps.index(sub_step)
    
    @pytest.mark.asyncio
    async def test_execute_transaction_success(self, assembler):
        """Тест успешного выполнения транзакции."""
        execution_log = []
        
        # Регистрируем обработчик
        async def mock_handler(entity_id, operation, data):
            execution_log.append((entity_id, operation, data))
            return True
        
        assembler.register_handler("subscription", mock_handler)
        
        changes = [
            {
                "entity_type": "subscription",
                "entity_id": "sub_123",
                "operation": "activate",
                "data": {"user_id": "user_1"},
                "dependencies": []
            }
        ]
        
        transaction = assembler.create_transaction(changes)
        success = await assembler.execute_transaction(transaction.transaction_id)
        
        assert success is True
        assert transaction.status == "completed"
        assert len(execution_log) == 1
        assert execution_log[0] == ("sub_123", "activate", {"user_id": "user_1"})
    
    @pytest.mark.asyncio
    async def test_execute_transaction_no_handler(self, assembler):
        """Тест выполнения без обработчика."""
        changes = [
            {
                "entity_type": "unknown_entity",
                "entity_id": "ue_1",
                "operation": "create",
                "data": {},
                "dependencies": []
            }
        ]
        
        transaction = assembler.create_transaction(changes)
        success = await assembler.execute_transaction(transaction.transaction_id)
        
        assert success is False
        assert transaction.status == "failed"
        assert "No handler registered" in transaction.error_message
    
    @pytest.mark.asyncio
    async def test_execute_transaction_retry(self, assembler):
        """Тест повторных попыток выполнения."""
        attempt_count = 0
        
        async def flaky_handler(entity_id, operation, data):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                return False  # Неудача первые 2 попытки
            return True  # Успех на 3й попытке
        
        assembler.register_handler("subscription", flaky_handler)
        
        changes = [
            {
                "entity_type": "subscription",
                "entity_id": "sub_123",
                "operation": "update",
                "data": {},
                "dependencies": []
            }
        ]
        
        transaction = assembler.create_transaction(changes)
        success = await assembler.execute_transaction(transaction.transaction_id)
        
        assert success is True
        assert attempt_count == 3
        assert transaction.status == "completed"
    
    def test_cancel_transaction(self, assembler):
        """Тест отмены транзакции."""
        changes = [
            {
                "entity_type": "subscription",
                "entity_id": "sub_123",
                "operation": "update",
                "data": {},
                "dependencies": []
            }
        ]
        
        transaction = assembler.create_transaction(changes)
        
        result = assembler.cancel_transaction(transaction.transaction_id)
        
        assert result is True
        assert transaction.status == "cancelled"
    
    def test_cancel_non_pending_transaction(self, assembler):
        """Тест отмены не pending транзакции."""
        changes = [
            {
                "entity_type": "subscription",
                "entity_id": "sub_123",
                "operation": "update",
                "data": {},
                "dependencies": []
            }
        ]
        
        transaction = assembler.create_transaction(changes)
        transaction.status = "executing"  # Меняем статус
        
        result = assembler.cancel_transaction(transaction.transaction_id)
        
        assert result is False
    
    def test_list_pending_transactions(self, assembler):
        """Тест списка pending транзакций."""
        changes = [
            {
                "entity_type": "subscription",
                "entity_id": f"sub_{i}",
                "operation": "update",
                "data": {},
                "dependencies": []
            }
            for i in range(3)
        ]
        
        for change in changes:
            assembler.create_transaction([change])
        
        pending = assembler.list_pending_transactions()
        
        assert len(pending) == 3
    
    def test_clear_completed(self, assembler):
        """Тест очистки завершенных транзакций."""
        # Создаем и завершаем транзакции
        for i in range(3):
            changes = [{
                "entity_type": "subscription",
                "entity_id": f"sub_{i}",
                "operation": "update",
                "data": {},
                "dependencies": []
            }]
            txn = assembler.create_transaction(changes)
            txn.status = "completed"
        
        # Создаем pending транзакцию
        pending_changes = [{
            "entity_type": "subscription",
            "entity_id": "sub_pending",
            "operation": "update",
            "data": {},
            "dependencies": []
        }]
        assembler.create_transaction(pending_changes)
        
        cleared = assembler.clear_completed()
        
        assert cleared == 3
        assert len(assembler.list_pending_transactions()) == 1
    
    def test_transaction_steps_priority(self, assembler):
        """Тест приоритетов шагов транзакции."""
        changes = [
            {
                "entity_type": "plan",
                "entity_id": "plan_1",
                "operation": "update",
                "data": {},
                "dependencies": []
            },
            {
                "entity_type": "subscription",
                "entity_id": "sub_1",
                "operation": "update",
                "data": {},
                "dependencies": ["plan:plan_1"]
            }
        ]
        
        transaction = assembler.create_transaction(changes)
        
        # Проверяем что приоритеты назначены по порядку
        for idx, step in enumerate(transaction.steps):
            assert step.priority == idx
    
    def test_generate_transaction_id_unique(self, assembler):
        """Тест уникальности ID транзакций."""
        ids = set()
        for _ in range(100):
            changes = [{
                "entity_type": "subscription",
                "entity_id": "sub_test",
                "operation": "update",
                "data": {},
                "dependencies": []
            }]
            txn = assembler.create_transaction(changes)
            ids.add(txn.transaction_id)
        
        # Все ID должны быть уникальными
        assert len(ids) == 100
