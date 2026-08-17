"""
Priority Dispatcher с пулами воркеров
Распределение задач по приоритетам и типам обработки.
"""
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from datetime import datetime
from collections import deque
import heapq
import uuid

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """Приоритеты задач (меньше = выше приоритет)."""
    CRITICAL = 0      # Критические операции (оплата, активация)
    HIGH = 10         # Высокий приоритет (синхронизация)
    NORMAL = 20       # Обычные операции (обновления)
    LOW = 30          # Низкий приоритет (логирование, статистика)
    BULK = 40         # Массовые операции (рассылки, отчеты)


class TaskStatus(str, Enum):
    """Статусы задачи."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PoolType(str, Enum):
    """Типы пулов воркеров."""
    FAST_POOL = "fast_pool"              # Быстрые операции (<100ms)
    TRANSACTION_POOL = "transaction_pool"  # Транзакционные операции
    DISTRIBUTED_POOL = "distributed_pool"  # Распределенные задачи
    BULK_POOL = "bulk_pool"              # Массовые операции


@dataclass(order=True)
class Task:
    """Задача для выполнения."""
    priority: int
    created_at: datetime = field(compare=False)
    task_id: str = field(compare=False, default_factory=lambda: str(uuid.uuid4()))
    pool_type: PoolType = field(compare=False, default=PoolType.NORMAL)
    handler: Callable = field(compare=False, default=None)
    args: tuple = field(compare=False, default_factory=tuple)
    kwargs: Dict[str, Any] = field(compare=False, default_factory=dict)
    status: TaskStatus = field(compare=False, default=TaskStatus.PENDING)
    result: Any = field(compare=False, default=None)
    error: Optional[str] = field(compare=False, default=None)
    retry_count: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)
    timeout: float = field(compare=False, default=30.0)
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()


class WorkerPool:
    """Пул воркеров для обработки задач определенного типа."""
    
    def __init__(
        self, 
        pool_type: PoolType, 
        max_workers: int = 5,
        default_priority: Priority = Priority.NORMAL
    ):
        self.pool_type = pool_type
        self.max_workers = max_workers
        self.default_priority = default_priority
        self._queue: List[Task] = []
        self._active_tasks: Dict[str, Task] = {}
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._lock = asyncio.Lock()
        self._stats = {
            'processed': 0,
            'failed': 0,
            'total_time': 0.0
        }
    
    async def start(self):
        """Запуск пула воркеров."""
        self._running = True
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self._workers.append(worker)
        logger.info(f"Started {self.pool_type.value} pool with {self.max_workers} workers")
    
    async def stop(self):
        """Остановка пула воркеров."""
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info(f"Stopped {self.pool_type.value} pool")
    
    async def submit(self, task: Task) -> str:
        """Добавление задачи в очередь."""
        async with self._lock:
            if task.pool_type != self.pool_type:
                raise ValueError(f"Task pool type {task.pool_type} doesn't match pool {self.pool_type}")
            
            task.status = TaskStatus.QUEUED
            heapq.heappush(self._queue, task)
            self._stats['queued'] = self._stats.get('queued', 0) + 1
        
        logger.debug(f"Submitted task {task.task_id} to {self.pool_type.value}")
        return task.task_id
    
    async def _worker(self, worker_name: str):
        """Воркер для обработки задач."""
        while self._running:
            task = None
            try:
                async with self._lock:
                    if self._queue:
                        task = heapq.heappop(self._queue)
                
                if not task:
                    await asyncio.sleep(0.1)
                    continue
                
                task.status = TaskStatus.RUNNING
                self._active_tasks[task.task_id] = task
                start_time = datetime.utcnow()
                
                try:
                    if asyncio.iscoroutinefunction(task.handler):
                        result = await asyncio.wait_for(
                            task.handler(*task.args, **task.kwargs),
                            timeout=task.timeout
                        )
                    else:
                        loop = asyncio.get_event_loop()
                        result = await asyncio.wait_for(
                            loop.run_in_executor(None, task.handler, *task.args, **task.kwargs),
                            timeout=task.timeout
                        )
                    
                    task.result = result
                    task.status = TaskStatus.COMPLETED
                    self._stats['processed'] += 1
                    
                except asyncio.TimeoutError:
                    task.error = f"Task timed out after {task.timeout}s"
                    task.status = TaskStatus.FAILED
                    self._stats['failed'] += 1
                    
                except Exception as e:
                    task.error = str(e)
                    task.status = TaskStatus.FAILED
                    self._stats['failed'] += 1
                    
                    # Retry logic
                    if task.retry_count < task.max_retries:
                        task.retry_count += 1
                        task.status = TaskStatus.PENDING
                        async with self._lock:
                            heapq.heappush(self._queue, task)
                        logger.warning(f"Retrying task {task.task_id}, attempt {task.retry_count}/{task.max_retries}")
                        continue
                
                finally:
                    end_time = datetime.utcnow()
                    elapsed = (end_time - start_time).total_seconds()
                    self._stats['total_time'] += elapsed
                    self._active_tasks.pop(task.task_id, None)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_name} error: {str(e)}")
                await asyncio.sleep(1)
    
    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики пула."""
        return {
            'pool_type': self.pool_type.value,
            'max_workers': self.max_workers,
            'active_tasks': len(self._active_tasks),
            'pending_tasks': len(self._queue),
            **self._stats
        }


class PriorityDispatcher:
    """
    Диспетчер приоритетов.
    Распределяет задачи по пулам воркеров на основе приоритета и типа.
    """
    
    def __init__(self):
        self._pools: Dict[PoolType, WorkerPool] = {}
        self._priority_mapping: Dict[Priority, PoolType] = {
            Priority.CRITICAL: PoolType.FAST_POOL,
            Priority.HIGH: PoolType.TRANSACTION_POOL,
            Priority.NORMAL: PoolType.DISTRIBUTED_POOL,
            Priority.LOW: PoolType.DISTRIBUTED_POOL,
            Priority.BULK: PoolType.BULK_POOL
        }
        self._running = False
    
    def register_pool(
        self, 
        pool_type: PoolType, 
        max_workers: int = 5,
        default_priority: Priority = Priority.NORMAL
    ):
        """Регистрация пула воркеров."""
        pool = WorkerPool(pool_type, max_workers, default_priority)
        self._pools[pool_type] = pool
        logger.info(f"Registered pool {pool_type.value} with {max_workers} workers")
    
    async def start(self):
        """Запуск всех пулов."""
        self._running = True
        for pool in self._pools.values():
            await pool.start()
        logger.info("Priority Dispatcher started")
    
    async def stop(self):
        """Остановка всех пулов."""
        self._running = False
        for pool in self._pools.values():
            await pool.stop()
        logger.info("Priority Dispatcher stopped")
    
    async def dispatch(
        self,
        handler: Callable,
        args: tuple = (),
        kwargs: Dict[str, Any] = None,
        priority: Priority = Priority.NORMAL,
        pool_type: Optional[PoolType] = None,
        timeout: float = 30.0,
        max_retries: int = 3
    ) -> str:
        """
        Диспетчеризация задачи.
        Автоматически выбирает пул на основе приоритета, если не указан явно.
        """
        if kwargs is None:
            kwargs = {}
        
        # Определение пула
        if pool_type is None:
            pool_type = self._priority_mapping.get(priority, PoolType.DISTRIBUTED_POOL)
        
        pool = self._pools.get(pool_type)
        if not pool:
            raise ValueError(f"Pool {pool_type.value} not registered")
        
        task = Task(
            priority=priority,
            handler=handler,
            args=args,
            kwargs=kwargs,
            pool_type=pool_type,
            timeout=timeout,
            max_retries=max_retries
        )
        
        return await pool.submit(task)
    
    def dispatch_sync(
        self,
        handler: Callable,
        args: tuple = (),
        kwargs: Dict[str, Any] = None,
        priority: Priority = Priority.NORMAL,
        pool_type: Optional[PoolType] = None,
        timeout: float = 30.0
    ) -> str:
        """Синхронная версия диспетчеризации (для тестов)."""
        if kwargs is None:
            kwargs = {}
        
        if pool_type is None:
            pool_type = self._priority_mapping.get(priority, PoolType.DISTRIBUTED_POOL)
        
        pool = self._pools.get(pool_type)
        if not pool:
            raise ValueError(f"Pool {pool_type.value} not registered")
        
        task = Task(
            priority=priority,
            handler=handler,
            args=args,
            kwargs=kwargs,
            pool_type=pool_type,
            timeout=timeout
        )
        
        # Для синхронного вызова просто добавляем в очередь
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Если цикл уже запущен, создаем задачу
                asyncio.create_task(pool.submit(task))
            else:
                loop.run_until_complete(pool.submit(task))
        except RuntimeError:
            # Нет активного цикла, создаем новый
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(pool.submit(task))
        
        return task.task_id
    
    def get_pool_stats(self, pool_type: PoolType) -> Dict[str, Any]:
        """Получение статистики пула."""
        pool = self._pools.get(pool_type)
        if not pool:
            return {}
        return pool.get_stats()
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Получение статистики всех пулов."""
        return {
            pool_type.value: pool.get_stats()
            for pool_type, pool in self._pools.items()
        }


# Глобальный экземпляр диспетчера
default_dispatcher = PriorityDispatcher()

def init_default_pools():
    """Инициализация пулов по умолчанию."""
    dispatcher = default_dispatcher
    
    # Fast pool для критических операций (оплата, активация)
    dispatcher.register_pool(PoolType.FAST_POOL, max_workers=10, default_priority=Priority.CRITICAL)
    
    # Transaction pool для транзакционных операций
    dispatcher.register_pool(PoolType.TRANSACTION_POOL, max_workers=5, default_priority=Priority.HIGH)
    
    # Distributed pool для обычных задач
    dispatcher.register_pool(PoolType.DISTRIBUTED_POOL, max_workers=8, default_priority=Priority.NORMAL)
    
    # Bulk pool для массовых операций
    dispatcher.register_pool(PoolType.BULK_POOL, max_workers=3, default_priority=Priority.BULK)
    
    logger.info("Default worker pools initialized")

init_default_pools()
