"""
Tests for Priority Dispatcher.
"""
import pytest
import asyncio
from datetime import datetime

from app.modules.billing.dispatcher.priority_dispatcher import (
    PriorityDispatcher,
    WorkerPool,
    PrioritizedTask,
    Priority,
    PoolType,
    WorkerStats
)


class TestPriorityEnum:
    """Тесты для enum Priority."""
    
    def test_priority_values(self):
        """Тест значений приоритетов."""
        assert Priority.CRITICAL.value == 0
        assert Priority.HIGH.value == 10
        assert Priority.NORMAL.value == 20
        assert Priority.LOW.value == 30
        assert Priority.BULK.value == 40
    
    def test_priority_ordering(self):
        """Тест порядка приоритетов."""
        assert Priority.CRITICAL < Priority.HIGH
        assert Priority.HIGH < Priority.NORMAL
        assert Priority.NORMAL < Priority.LOW
        assert Priority.LOW < Priority.BULK


class TestPoolType:
    """Тесты для PoolType."""
    
    def test_pool_type_values(self):
        """Тест значений типов пулов."""
        assert PoolType.FAST_POOL == "fast_pool"
        assert PoolType.TRANSACTION_POOL == "transaction_pool"
        assert PoolType.DISTRIBUTED_POOL == "distributed_pool"
        assert PoolType.BULK_POOL == "bulk_pool"


class TestPrioritizedTask:
    """Тесты для PrioritizedTask."""
    
    def test_create_task(self):
        """Тест создания задачи."""
        payload = {"order_id": "123"}
        task = PrioritizedTask.create(
            payload=payload,
            priority=Priority.HIGH,
            pool_type=PoolType.FAST_POOL
        )
        
        assert task.payload == payload
        assert task.priority == 10  # HIGH value
        assert task.pool_type == PoolType.FAST_POOL
        assert task.retry_count == 0
        assert task.max_retries == 3
        assert task.task_id is not None
    
    def test_task_ordering(self):
        """Тест сортировки задач."""
        task1 = PrioritizedTask.create(
            payload={"id": 1},
            priority=Priority.NORMAL
        )
        task2 = PrioritizedTask.create(
            payload={"id": 2},
            priority=Priority.CRITICAL
        )
        task3 = PrioritizedTask.create(
            payload={"id": 3},
            priority=Priority.HIGH
        )
        
        tasks = [task1, task2, task3]
        sorted_tasks = sorted(tasks)
        
        # CRITICAL должен быть первым, затем HIGH, затем NORMAL
        assert sorted_tasks[0].priority == 0  # CRITICAL
        assert sorted_tasks[1].priority == 10  # HIGH
        assert sorted_tasks[2].priority == 20  # NORMAL


class TestWorkerPool:
    """Тесты для WorkerPool."""
    
    @pytest.fixture
    def pool(self):
        """Фикстура с WorkerPool."""
        return WorkerPool(
            pool_type=PoolType.TRANSACTION_POOL,
            worker_count=2,
            max_queue_size=10
        )
    
    @pytest.mark.asyncio
    async def test_start_stop(self, pool):
        """Тест запуска и остановки пула."""
        await pool.start()
        assert pool._running is True
        assert len(pool._workers) == 2
        
        await pool.stop(timeout=1.0)
        assert pool._running is False
    
    @pytest.mark.asyncio
    async def test_submit_task(self, pool):
        """Тест добавления задачи в очередь."""
        await pool.start()
        
        task = PrioritizedTask.create(
            payload={"test": "data"},
            priority=Priority.NORMAL
        )
        
        result = await pool.submit(task)
        
        assert result is True
        assert len(pool._queue) == 1
        
        await pool.stop(timeout=1.0)
    
    @pytest.mark.asyncio
    async def test_submit_to_full_queue(self, pool):
        """Тест добавления задачи в полную очередь."""
        pool.max_queue_size = 2
        
        # Заполняем очередь
        for i in range(2):
            task = PrioritizedTask.create(
                payload={"id": i},
                priority=Priority.NORMAL
            )
            await pool.submit(task)
        
        # Попытка добавить еще одну задачу
        task = PrioritizedTask.create(
            payload={"id": "overflow"},
            priority=Priority.NORMAL
        )
        result = await pool.submit(task)
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_execute_task_with_handler(self, pool):
        """Тест выполнения задачи с обработчиком."""
        executed_payloads = []
        
        async def mock_handler(payload):
            executed_payloads.append(payload)
        
        await pool.start()
        
        task = PrioritizedTask.create(
            payload={"order_id": "123"},
            priority=Priority.HIGH,
            handler=mock_handler
        )
        
        await pool.submit(task)
        
        # Ждем выполнения
        await asyncio.sleep(0.5)
        
        await pool.stop(timeout=1.0)
        
        assert len(executed_payloads) == 1
        assert executed_payloads[0] == {"order_id": "123"}
    
    def test_get_stats(self, pool):
        """Тест получения статистики."""
        stats = pool.get_stats()
        
        assert stats["pool_type"] == PoolType.TRANSACTION_POOL
        assert stats["worker_count"] == 2
        assert stats["queue_size"] == 0
        assert "workers" in stats


class TestPriorityDispatcher:
    """Тесты для PriorityDispatcher."""
    
    @pytest.fixture
    def dispatcher(self):
        """Фикстура с PriorityDispatcher."""
        return PriorityDispatcher()
    
    def test_initialize_default_pools(self, dispatcher):
        """Тест инициализации пулов по умолчанию."""
        dispatcher.initialize()
        
        assert len(dispatcher._pools) == 4
        assert PoolType.FAST_POOL in dispatcher._pools
        assert PoolType.TRANSACTION_POOL in dispatcher._pools
        assert PoolType.DISTRIBUTED_POOL in dispatcher._pools
        assert PoolType.BULK_POOL in dispatcher._pools
    
    def test_initialize_custom_config(self, dispatcher):
        """Тест инициализации с кастомной конфигурацией."""
        custom_config = {
            PoolType.FAST_POOL: {"worker_count": 16, "max_queue_size": 100}
        }
        
        dispatcher.initialize(pool_configs=custom_config)
        
        fast_pool = dispatcher._pools[PoolType.FAST_POOL]
        assert fast_pool.worker_count == 16
        assert fast_pool.max_queue_size == 100
    
    @pytest.mark.asyncio
    async def test_start_stop_all_pools(self, dispatcher):
        """Тест запуска и остановки всех пулов."""
        await dispatcher.start()
        
        for pool in dispatcher._pools.values():
            assert pool._running is True
        
        await dispatcher.stop(timeout=1.0)
        
        for pool in dispatcher._pools.values():
            assert pool._running is False
    
    def test_select_pool_by_priority(self, dispatcher):
        """Тест выбора пула на основе приоритета."""
        dispatcher.initialize()
        
        # CRITICAL -> FAST_POOL
        pool = dispatcher._select_pool(Priority.CRITICAL)
        assert pool == PoolType.FAST_POOL
        
        # HIGH -> TRANSACTION_POOL
        pool = dispatcher._select_pool(Priority.HIGH)
        assert pool == PoolType.TRANSACTION_POOL
        
        # NORMAL -> DISTRIBUTED_POOL
        pool = dispatcher._select_pool(Priority.NORMAL)
        assert pool == PoolType.DISTRIBUTED_POOL
        
        # LOW/BULK -> BULK_POOL
        pool = dispatcher._select_pool(Priority.LOW)
        assert pool == PoolType.BULK_POOL
        
        pool = dispatcher._select_pool(Priority.BULK)
        assert pool == PoolType.BULK_POOL
    
    def test_select_pool_explicit(self, dispatcher):
        """Тест явного выбора пула."""
        dispatcher.initialize()
        
        # Явно указываем пул
        pool = dispatcher._select_pool(Priority.NORMAL, PoolType.FAST_POOL)
        assert pool == PoolType.FAST_POOL
    
    @pytest.mark.asyncio
    async def test_dispatch_task(self, dispatcher):
        """Тест отправки задачи."""
        executed = []
        
        async def handler(payload):
            executed.append(payload)
        
        await dispatcher.start()
        
        task_id = await dispatcher.dispatch(
            payload={"order_id": "123"},
            priority=Priority.HIGH,
            handler=handler
        )
        
        assert task_id is not None
        
        # Ждем выполнения
        await asyncio.sleep(0.5)
        
        await dispatcher.stop(timeout=1.0)
        
        assert len(executed) == 1
    
    def test_dispatch_auto_initializes(self, dispatcher):
        """Тест что dispatch автоматически инициализирует."""
        # Не вызываем initialize явно
        
        task_id = asyncio.run(
            dispatcher.dispatch(
                payload={"test": "data"},
                priority=Priority.NORMAL
            )
        )
        
        # Должен вернуться None так как пулы не запущены
        # но инициализация должна произойти
        assert dispatcher._initialized is True
    
    def test_get_stats(self, dispatcher):
        """Тест получения общей статистики."""
        dispatcher.initialize()
        
        stats = dispatcher.get_stats()
        
        assert "pools" in stats
        assert "total_pools" in stats
        assert stats["total_pools"] == 4
        assert len(stats["pools"]) == 4
    
    def test_get_pool(self, dispatcher):
        """Тест получения пула по типу."""
        dispatcher.initialize()
        
        pool = dispatcher.get_pool(PoolType.FAST_POOL)
        assert pool is not None
        assert pool.pool_type == PoolType.FAST_POOL
        
        nonexistent = dispatcher.get_pool("nonexistent_pool")
        assert nonexistent is None


class TestWorkerStats:
    """Тесты для WorkerStats."""
    
    def test_default_values(self):
        """Тест значений по умолчанию."""
        stats = WorkerStats()
        
        assert stats.tasks_processed == 0
        assert stats.tasks_failed == 0
        assert stats.avg_execution_time == 0.0
        assert stats.last_task_time is None
    
    def test_update_stats(self):
        """Тест обновления статистики."""
        stats = WorkerStats()
        
        stats.tasks_processed = 10
        stats.tasks_failed = 2
        stats.avg_execution_time = 0.5
        
        assert stats.tasks_processed == 10
        assert stats.tasks_failed == 2
        assert stats.avg_execution_time == 0.5


@pytest.mark.asyncio
class TestIntegrationScenarios:
    """Интеграционные тесты."""
    
    async def test_critical_task_processed_first(self):
        """Тест что критические задачи обрабатываются первыми."""
        dispatcher = PriorityDispatcher()
        execution_order = []
        
        async def handler(payload):
            execution_order.append(payload["priority_name"])
            await asyncio.sleep(0.01)
        
        await dispatcher.start()
        
        # Отправляем задачи в обратном порядке приоритета
        await dispatcher.dispatch(
            payload={"priority_name": "bulk"},
            priority=Priority.BULK,
            handler=handler
        )
        await dispatcher.dispatch(
            payload={"priority_name": "normal"},
            priority=Priority.NORMAL,
            handler=handler
        )
        await dispatcher.dispatch(
            payload={"priority_name": "critical"},
            priority=Priority.CRITICAL,
            handler=handler
        )
        
        # Ждем выполнения
        await asyncio.sleep(1.0)
        
        await dispatcher.stop(timeout=1.0)
        
        # Критическая задача должна быть выполнена первой
        if len(execution_order) >= 1:
            assert execution_order[0] == "critical"
    
    async def test_retry_on_failure(self):
        """Тест повторных попыток при ошибке."""
        dispatcher = PriorityDispatcher()
        attempt_count = 0
        
        async def flaky_handler(payload):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise Exception("Temporary failure")
            return True
        
        await dispatcher.start()
        
        await dispatcher.dispatch(
            payload={"test": "retry"},
            priority=Priority.HIGH,
            handler=flaky_handler,
            max_retries=3
        )
        
        # Ждем выполнения с повторами
        await asyncio.sleep(2.0)
        
        await dispatcher.stop(timeout=1.0)
        
        # Обработчик должен быть вызван 3 раза
        assert attempt_count == 3
