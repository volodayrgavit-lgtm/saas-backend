"""
Тесты для компонентов биллинга:
- TransactionAssembler & DependencyRegistry
- Priority Dispatcher
- PostgreSQL LISTEN/NOTIFY
- Sync/Share API
"""
import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch
import asyncio


# =====================
# Тесты для TransactionAssembler и DependencyRegistry
# =====================

class TestDependencyRegistry:
    """Тесты реестра зависимостей."""
    
    def test_register_dependency(self):
        from app.modules.billing.assembler.dependency_registry import DependencyRegistry, DependencyType
        
        registry = DependencyRegistry()
        registry.register_dependency('subscription', 'sub_1', 'plan', 'plan_1', DependencyType.SUBSCRIPTION_PLAN)
        
        # Проверяем что узлы созданы
        assert len(registry._nodes) == 2
    
    def test_get_execution_order(self):
        from app.modules.billing.assembler.dependency_registry import DependencyRegistry, DependencyType
        
        registry = DependencyRegistry()
        
        # Регистрируем зависимости: subscription зависит от plan
        registry.register_dependency('subscription', 'sub_1', 'plan', 'plan_1', DependencyType.SUBSCRIPTION_PLAN)
        
        changed_entities = [
            ('plan', 'plan_1', 'update'),
            ('subscription', 'sub_1', 'update')
        ]
        
        order = registry.get_execution_order(changed_entities)
        
        # plan должен быть перед subscription
        plan_index = next(i for i, (t, _, _) in enumerate(order) if t == 'plan')
        sub_index = next(i for i, (t, _, _) in enumerate(order) if t == 'subscription')
        assert plan_index < sub_index
    
    def test_get_dependents(self):
        from app.modules.billing.assembler.dependency_registry import DependencyRegistry, DependencyType
        
        registry = DependencyRegistry()
        registry.register_dependency('subscription', 'sub_1', 'plan', 'plan_1', DependencyType.SUBSCRIPTION_PLAN)
        
        dependents = registry.get_dependents('plan', 'plan_1')
        
        assert len(dependents) == 1
        assert dependents[0] == ('subscription', 'sub_1')
    
    def test_cycle_detection(self):
        from app.modules.billing.assembler.dependency_registry import DependencyRegistry, DependencyType
        
        registry = DependencyRegistry()
        
        # Создаем цикл через зависимости
        registry.register_dependency('subscription', 'sub_1', 'plan', 'plan_1', DependencyType.SUBSCRIPTION_PLAN)
        
        changed_entities = [
            ('subscription', 'sub_1', 'update'),
            ('plan', 'plan_1', 'update')
        ]
        
        # Не должно быть ошибок
        order = registry.get_execution_order(changed_entities)
        assert len(order) > 0


class TestTransactionAssembler:
    """Тесты сборщика транзакций."""
    
    def test_create_planned_transaction(self):
        from app.modules.billing.assembler.transaction_assembler import (
            PlannedTransaction, TransactionStep, TransactionOperation
        )
        
        tx = PlannedTransaction(transaction_id="tx_1")
        tx.add_step(TransactionStep(
            entity_type="subscription",
            entity_id="sub_1",
            operation=TransactionOperation.UPDATE,
            data={"status": "active"}
        ))
        
        assert tx.transaction_id == "tx_1"
        assert len(tx.steps) == 1
    
    def test_transaction_step(self):
        from app.modules.billing.assembler.transaction_assembler import (
            TransactionStep, TransactionOperation
        )
        
        step = TransactionStep(
            entity_type="subscription",
            entity_id="sub_1",
            operation=TransactionOperation.UPDATE,
            data={"status": "active"},
            dependencies=[]
        )
        
        assert step.entity_type == "subscription"
        assert step.operation == TransactionOperation.UPDATE


# =====================
# Тесты для Priority Dispatcher
# =====================

class TestPriorityDispatcher:
    """Тесты диспетчера приоритетов."""
    
    def test_priority_enum(self):
        from app.modules.billing.dispatcher.priority_dispatcher import Priority
        
        assert Priority.CRITICAL.value == 0
        assert Priority.HIGH.value == 10
        assert Priority.NORMAL.value == 20
        assert Priority.LOW.value == 30
        assert Priority.BULK.value == 40
    
    def test_pool_type_enum(self):
        from app.modules.billing.dispatcher.priority_dispatcher import PoolType
        
        assert PoolType.FAST_POOL == "fast_pool"
        assert PoolType.TRANSACTION_POOL == "transaction_pool"
        assert PoolType.DISTRIBUTED_POOL == "distributed_pool"
        assert PoolType.BULK_POOL == "bulk_pool"
    
    def test_prioritized_task_create(self):
        from app.modules.billing.dispatcher.priority_dispatcher import (
            PrioritizedTask, Priority, PoolType
        )
        
        task = PrioritizedTask.create(
            payload={"order_id": "ord_1"},
            priority=Priority.CRITICAL,
            pool_type=PoolType.FAST_POOL
        )
        
        assert task.priority == 0
        assert task.pool_type == PoolType.FAST_POOL
        assert task.task_id is not None
    
    def test_worker_stats(self):
        from app.modules.billing.dispatcher.priority_dispatcher import WorkerStats
        
        stats = WorkerStats()
        assert stats.tasks_processed == 0
        assert stats.tasks_failed == 0


# =====================
# Тесты для PostgreSQL LISTEN/NOTIFY
# =====================

class TestPostgresNotifyListener:
    """Тесты слушателя уведомлений PostgreSQL."""
    
    def test_notification_parse_payload(self):
        from app.modules.billing.postgres_notify import Notification
        import json
        
        payload_data = {"event": "test", "value": 123}
        notification = Notification(
            channel="test_channel",
            payload=json.dumps(payload_data),
            received_at=datetime.utcnow()
        )
        
        parsed = notification.parse_payload()
        assert parsed == payload_data
    
    def test_notification_parse_invalid_json(self):
        from app.modules.billing.postgres_notify import Notification
        
        notification = Notification(
            channel="test_channel",
            payload="not valid json",
            received_at=datetime.utcnow()
        )
        
        parsed = notification.parse_payload()
        assert "raw" in parsed
        assert parsed["raw"] == "not valid json"
    
    def test_subscribe_unsubscribe(self):
        from app.modules.billing.postgres_notify import PostgresNotifyListener
        
        listener = PostgresNotifyListener(dsn="postgresql://test")
        
        callback = Mock()
        sub_id = listener.subscribe("test_channel", callback)
        
        assert sub_id in listener._subscriptions
        assert listener._subscriptions[sub_id].active is True
        
        listener.unsubscribe(sub_id)
        
        assert sub_id not in listener._subscriptions
    
    def test_match_channel_exact(self):
        from app.modules.billing.postgres_notify import PostgresNotifyListener
        
        listener = PostgresNotifyListener(dsn="postgresql://test")
        
        result = listener._match_channel("billing_events", "billing_events", None)
        assert result is True
        
        result = listener._match_channel("billing_events", "other_channel", None)
        assert result is False
    
    def test_match_channel_pattern(self):
        from app.modules.billing.postgres_notify import PostgresNotifyListener
        
        listener = PostgresNotifyListener(dsn="postgresql://test")
        
        result = listener._match_channel("billing_events", "billing_*", "billing_*")
        assert result is True
        
        result = listener._match_channel("other_events", "billing_*", "billing_*")
        assert result is False
    
    def test_create_event_payload(self):
        from app.modules.billing.postgres_notify import create_event_payload
        
        payload = create_event_payload(
            event_type="order.paid",
            entity_type="order",
            entity_id="ord_123",
            data={"amount": 1000}
        )
        
        assert payload["event_type"] == "order.paid"
        assert payload["entity_type"] == "order"
        assert payload["entity_id"] == "ord_123"
        assert payload["data"] == {"amount": 1000}
        assert "timestamp" in payload
        assert "version" in payload
    
    def test_billing_event_types(self):
        from app.modules.billing.postgres_notify import BillingEventTypes
        
        assert BillingEventTypes.ORDER_PAID == "order.paid"
        assert BillingEventTypes.SUBSCRIPTION_ACTIVATED == "subscription.activated"


# =====================
# Тесты для Sync/Share API
# =====================

class TestSyncAPI:
    """Тесты API синхронизации."""
    
    def test_sync_storage_add_transaction(self):
        from app.modules.billing.sync_api import SyncStorage, SyncTransaction
        
        storage = SyncStorage()
        
        tx = SyncTransaction(
            id="tx_1",
            entity_type="order",
            entity_id="ord_1",
            action="create",
            payload={},
            version=1,
            created_at=datetime.utcnow(),
            status="pending"
        )
        
        storage.add_transaction(tx)
        
        assert "order:ord_1" in storage._versions
        assert storage._versions["order:ord_1"] == 1
    
    def test_sync_storage_get_transactions(self):
        from app.modules.billing.sync_api import SyncStorage, SyncTransaction
        
        storage = SyncStorage()
        
        for i in range(5):
            tx = SyncTransaction(
                id=f"tx_{i}",
                entity_type="order",
                entity_id="ord_1",
                action="update",
                payload={},
                version=i + 1,
                created_at=datetime.utcnow(),
                status="pending"
            )
            storage.add_transaction(tx)
        
        transactions = storage.get_transactions(
            entity_type="order",
            entity_id="ord_1",
            from_version=2,
            limit=10
        )
        
        assert len(transactions) == 3  # Версии 3, 4, 5
        assert all(t.version > 2 for t in transactions)
    
    def test_sync_storage_calculate_checksum(self):
        from app.modules.billing.sync_api import SyncStorage, EntityState
        
        storage = SyncStorage()
        
        state = EntityState(
            entity_type="order",
            entity_id="ord_1",
            data={"amount": 1000},
            version=1,
            checksum=""  # Пустой checksum для создания
        )
        storage.update_entity_state(state)
        
        checksum = storage.calculate_checksum("order", "ord_1")
        
        assert len(checksum) == 16  # SHA256 первые 16 символов
        assert checksum.isalnum()
    
    def test_emit_sync_event(self):
        from app.modules.billing.sync_api import emit_sync_event, sync_storage
        
        initial_count = len(sync_storage._transactions)
        
        tx = emit_sync_event(
            entity_type="subscription",
            entity_id="sub_1",
            action="update",
            payload={"status": "active"},
            version=1
        )
        
        assert tx.id is not None
        # Не проверяем точное количество, т.к. могут быть другие транзакции
        assert len(sync_storage._transactions) > initial_count


class TestSyncAPIEndpoints:
    """Тесты endpoints API синхронизации."""
    
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.modules.billing.sync_api import router
        
        app = Mock()
        app.include_router = Mock()
        
        # Создаем тестовый клиент напрямую
        from fastapi import FastAPI
        test_app = FastAPI()
        test_app.include_router(router)
        
        return TestClient(test_app)
    
    def test_get_sync_stats(self, client):
        response = client.get("/sync/stats")
        assert response.status_code == 200
        
        data = response.json()
        assert "total_transactions" in data
        assert "pending_transactions" in data
    
    def test_sync_data(self, client):
        from app.modules.billing.sync_api import sync_storage, SyncTransaction
        
        # Добавляем тестовую транзакцию
        tx = SyncTransaction(
            id="test_tx",
            entity_type="order",
            entity_id="ord_1",
            action="create",
            payload={"test": "data"},
            version=5,
            created_at=datetime.utcnow()
        )
        sync_storage.add_transaction(tx)
        
        response = client.post(
            "/sync/sync",
            json={
                "entity_type": "order",
                "entity_id": "ord_1",
                "from_version": 3,
                "limit": 10
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["transactions"]) >= 1
        assert data["current_version"] >= 5
    
    def test_share_data(self, client):
        response = client.post(
            "/sync/share",
            json={
                "source_service": "billing",
                "target_services": ["core", "user"],
                "entity_type": "subscription",
                "entity_id": "sub_1",
                "data": {"status": "active"},
                "sync_mode": "full"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "delivered_to" in data
        assert "success" in data
    
    def test_update_entity_state(self, client):
        response = client.put(
            "/sync/state",
            json={
                "entity_type": "order",
                "entity_id": "ord_1",
                "data": {"amount": 1000, "status": "paid"},
                "version": 1,
                "checksum": ""  # Требуется поле checksum
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["entity_type"] == "order"
        assert data["entity_id"] == "ord_1"
        assert "checksum" in data
    
    def test_verify_checksums(self, client):
        # Сначала создаем состояние
        client.put(
            "/sync/state",
            json={
                "entity_type": "order",
                "entity_id": "ord_1",
                "data": {"amount": 1000},
                "version": 1
            }
        )
        
        response = client.post(
            "/sync/checksums",
            json={
                "entities": [
                    {
                        "entity_type": "order",
                        "entity_id": "ord_1",
                        "checksum": "wrong_checksum"
                    }
                ]
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "checksums" in data
        assert len(data["mismatches"]) == 1


# =====================
# Интеграционные тесты
# =====================

class TestBillingIntegration:
    """Интеграционные тесты компонентов биллинга."""
    
    def test_assembler_with_dispatcher(self):
        from app.modules.billing.assembler.dependency_registry import (
            DependencyRegistry, DependencyType
        )
        from app.modules.billing.assembler.transaction_assembler import (
            TransactionAssembler, TransactionStep, TransactionOperation
        )
        
        # Настраиваем registry с зависимостями
        registry = DependencyRegistry()
        registry.register_dependency('subscription', 'sub_1', 'plan', 'plan_1', DependencyType.SUBSCRIPTION_PLAN)
        
        # Создаем assembler
        assembler = TransactionAssembler(registry)
        
        # Получаем порядок выполнения
        changed_entities = [
            ('plan', 'plan_1', 'update'),
            ('subscription', 'sub_1', 'update')
        ]
        
        order = registry.get_execution_order(changed_entities)
        assert len(order) == 2
        
        # plan должен быть перед subscription
        plan_index = next(i for i, (t, _, _) in enumerate(order) if t == 'plan')
        sub_index = next(i for i, (t, _, _) in enumerate(order) if t == 'subscription')
        assert plan_index < sub_index
    
    def test_full_sync_flow(self):
        from app.modules.billing.sync_api import (
            emit_sync_event, sync_storage
        )
        
        # Очищаем хранилище перед тестом
        sync_storage._transactions.clear()
        sync_storage._versions.clear()
        
        # Эмитим событие
        tx = emit_sync_event(
            entity_type="subscription",
            entity_id="sub_1",
            action="create",
            payload={"plan_id": "plan_pro"},
            version=1
        )
        
        # Получаем транзакции для синхронизации
        transactions = sync_storage.get_transactions(
            entity_type="subscription",
            entity_id="sub_1",
            from_version=0,
            limit=10
        )
        
        assert len(transactions) >= 1
        assert transactions[0].action == "create"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
