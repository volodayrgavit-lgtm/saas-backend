"""
Priority Dispatcher с пулами воркеров для обработки задач биллинга.
Реализует несколько классов приоритетов и пулов воркеров.
"""
import asyncio
import logging
from typing import Dict, List, Any, Optional, Callable, Awaitable, TypeVar, Generic
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from collections import defaultdict
import heapq
import uuid

logger = logging.getLogger(__name__)


T = TypeVar('T')


class Priority(IntEnum):
    """Классы приоритетов задач (чем меньше число, тем выше приоритет)."""
    CRITICAL = 0      # Критические задачи (обработка платежей)
    HIGH = 10         # Высокий приоритет (активация подписок)
    NORMAL = 20       # Нормальный приоритет (синхронизация)
    LOW = 30          # Низкий приоритет (отчетность)
    BULK = 40         # Массовые операции (рассылки, bulk обновления)


class PoolType(str):
    """Типы пулов воркеров."""
    FAST_POOL = "fast_pool"           # Быстрые задачи (< 100ms)
    TRANSACTION_POOL = "transaction_pool"  # Транзакционные задачи
    DISTRIBUTED_POOL = "distributed_pool"  # Распределенные задачи
    BULK_POOL = "bulk_pool"           # Массовые операции


@dataclass(order=True)
class PrioritizedTask(Generic[T]):
    """Задача с приоритетом для очереди."""
    priority: int
    created_at: float
    task_id: str = field(compare=False)
    payload: T = field(compare=False)
    pool_type: str = field(compare=False, default=PoolType.TRANSACTION_POOL)
    retry_count: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)
    handler: Optional[Callable] = field(compare=False, default=None)
    
    @classmethod
    def create(
        cls,
        payload: T,
        priority: Priority,
        pool_type: str = PoolType.TRANSACTION_POOL,
        handler: Optional[Callable] = None,
        max_retries: int = 3
    ) -> 'PrioritizedTask[T]':
        """Создает новую задачу."""
        return cls(
            priority=priority.value,
            created_at=datetime.utcnow().timestamp(),
            task_id=str(uuid.uuid4()),
            payload=payload,
            pool_type=pool_type,
            handler=handler,
            max_retries=max_retries
        )


@dataclass
class WorkerStats:
    """Статистика воркера."""
    tasks_processed: int = 0
    tasks_failed: int = 0
    avg_execution_time: float = 0.0
    last_task_time: Optional[datetime] = None


class WorkerPool:
    """Пул воркеров для обработки задач определенного типа."""
    
    def __init__(
        self,
        pool_type: str,
        worker_count: int = 4,
        max_queue_size: int = 1000
    ):
        self.pool_type = pool_type
        self.worker_count = worker_count
        self.max_queue_size = max_queue_size
        
        self._queue: List[PrioritizedTask] = []
        self._workers: List[asyncio.Task] = []
        self._stats: Dict[str, WorkerStats] = {}
        self._running = False
        self._lock = asyncio.Lock()
        self._task_added = asyncio.Event()
        
        logger.info(f"Initialized {pool_type} with {worker_count} workers")
    
    async def start(self) -> None:
        """Запускает пул воркеров."""
        if self._running:
            logger.warning(f"Pool {self.pool_type} is already running")
            return
        
        self._running = True
        for i in range(self.worker_count):
            worker_id = f"{self.pool_type}_worker_{i}"
            worker = asyncio.create_task(self._worker_loop(worker_id))
            self._workers.append(worker)
            self._stats[worker_id] = WorkerStats()
        
        logger.info(f"Started {self.worker_count} workers in {self.pool_type}")
    
    async def stop(self, timeout: float = 5.0) -> None:
        """Останавливает пул воркеров."""
        self._running = False
        self._task_added.set()  # Разблокируем ожидающие воркеры
        
        # Ждем завершения текущих задач
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._workers, return_exceptions=True),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout stopping pool {self.pool_type}, cancelling workers")
            for worker in self._workers:
                worker.cancel()
        
        self._workers.clear()
        logger.info(f"Stopped pool {self.pool_type}")
    
    async def submit(self, task: PrioritizedTask) -> bool:
        """
        Добавляет задачу в очередь.
        
        Args:
            task: Задача для добавления
            
        Returns:
            True если задача добавлена успешно, False если очередь полна
        """
        async with self._lock:
            if len(self._queue) >= self.max_queue_size:
                logger.warning(f"Queue full for {self.pool_type}, rejecting task")
                return False
            
            heapq.heappush(self._queue, task)
            self._task_added.set()
            
            logger.debug(
                f"Submitted task {task.task_id} to {self.pool_type} "
                f"(priority={task.priority}, queue_size={len(self._queue)})"
            )
            return True
    
    async def _worker_loop(self, worker_id: str) -> None:
        """Цикл работы воркера."""
        while self._running:
            task = None
            
            try:
                async with self._lock:
                    if self._queue:
                        task = heapq.heappop(self._queue)
                    else:
                        self._task_added.clear()
                
                if task is None:
                    # Ждем появления задач
                    try:
                        await asyncio.wait_for(self._task_added.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                
                if task is None and self._queue:
                    async with self._lock:
                        if self._queue:
                            task = heapq.heappop(self._queue)
                
                if task:
                    await self._execute_task(task, worker_id)
                    
            except asyncio.CancelledError:
                logger.info(f"Worker {worker_id} cancelled")
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
    
    async def _execute_task(self, task: PrioritizedTask, worker_id: str) -> None:
        """Выполняет задачу."""
        start_time = datetime.utcnow()
        stats = self._stats.get(worker_id)
        
        try:
            if task.handler:
                if asyncio.iscoroutinefunction(task.handler):
                    await task.handler(task.payload)
                else:
                    task.handler(task.payload)
            
            execution_time = (datetime.utcnow() - start_time).total_seconds()
            
            if stats:
                stats.tasks_processed += 1
                stats.last_task_time = datetime.utcnow()
                # Обновляем среднее время выполнения
                n = stats.tasks_processed
                stats.avg_execution_time = ((n - 1) * stats.avg_execution_time + execution_time) / n
            
            logger.debug(
                f"Task {task.task_id} completed by {worker_id} "
                f"in {execution_time:.3f}s"
            )
            
        except Exception as e:
            logger.error(f"Task {task.task_id} failed: {e}")
            
            if stats:
                stats.tasks_failed += 1
            
            # Повторная попытка
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                task.priority += 5  # Немного повышаем приоритет при повторе
                await self.submit(task)
                logger.info(f"Rescheduled task {task.task_id} (attempt {task.retry_count})")
            else:
                logger.error(f"Task {task.task_id} failed after {task.max_retries} attempts")
    
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику пула."""
        total_queued = len(self._queue)
        total_processed = sum(s.tasks_processed for s in self._stats.values())
        total_failed = sum(s.tasks_failed for s in self._stats.values())
        
        return {
            "pool_type": self.pool_type,
            "worker_count": self.worker_count,
            "queue_size": total_queued,
            "total_processed": total_processed,
            "total_failed": total_failed,
            "workers": {
                wid: {
                    "tasks_processed": stats.tasks_processed,
                    "tasks_failed": stats.tasks_failed,
                    "avg_execution_time": stats.avg_execution_time,
                    "last_task_time": stats.last_task_time.isoformat() if stats.last_task_time else None
                }
                for wid, stats in self._stats.items()
            }
        }


class PriorityDispatcher:
    """
    Диспетчер приоритетов для распределения задач по пулам воркеров.
    """
    
    def __init__(self):
        self._pools: Dict[str, WorkerPool] = {}
        self._default_pool = PoolType.TRANSACTION_POOL
        self._initialized = False
        
        # Конфигурация пулов по умолчанию
        self._pool_configs = {
            PoolType.FAST_POOL: {"worker_count": 8, "max_queue_size": 500},
            PoolType.TRANSACTION_POOL: {"worker_count": 4, "max_queue_size": 1000},
            PoolType.DISTRIBUTED_POOL: {"worker_count": 2, "max_queue_size": 2000},
            PoolType.BULK_POOL: {"worker_count": 1, "max_queue_size": 5000},
        }
    
    def initialize(self, pool_configs: Optional[Dict[str, Dict]] = None) -> None:
        """
        Инициализирует пулы воркеров.
        
        Args:
            pool_configs: Опциональная конфигурация пулов
        """
        if self._initialized:
            logger.warning("PriorityDispatcher already initialized")
            return
        
        configs = pool_configs or self._pool_configs
        
        for pool_type, config in configs.items():
            pool = WorkerPool(
                pool_type=pool_type,
                worker_count=config.get("worker_count", 4),
                max_queue_size=config.get("max_queue_size", 1000)
            )
            self._pools[pool_type] = pool
        
        self._initialized = True
        logger.info(f"Initialized PriorityDispatcher with {len(self._pools)} pools")
    
    async def start(self) -> None:
        """Запускает все пулы воркеров."""
        if not self._initialized:
            self.initialize()
        
        for pool in self._pools.values():
            await pool.start()
        
        logger.info("All worker pools started")
    
    async def stop(self, timeout: float = 5.0) -> None:
        """Останавливает все пулы воркеров."""
        for pool in self._pools.values():
            await pool.stop(timeout=timeout)
        
        logger.info("All worker pools stopped")
    
    def _select_pool(self, priority: Priority, pool_type: Optional[str] = None) -> str:
        """
        Выбирает подходящий пул для задачи.
        
        Args:
            priority: Приоритет задачи
            pool_type: Предпочтительный тип пула
            
        Returns:
            Тип пула для задачи
        """
        if pool_type and pool_type in self._pools:
            return pool_type
        
        # Автоматический выбор на основе приоритета
        if priority == Priority.CRITICAL:
            return PoolType.FAST_POOL
        elif priority == Priority.HIGH:
            return PoolType.TRANSACTION_POOL
        elif priority == Priority.NORMAL:
            return PoolType.DISTRIBUTED_POOL
        else:
            return PoolType.BULK_POOL
    
    async def dispatch(
        self,
        payload: Any,
        priority: Priority = Priority.NORMAL,
        pool_type: Optional[str] = None,
        handler: Optional[Callable] = None,
        max_retries: int = 3
    ) -> Optional[str]:
        """
        Отправляет задачу в соответствующий пул.
        
        Args:
            payload: Данные задачи
            priority: Приоритет задачи
            pool_type: Тип пула (опционально)
            handler: Обработчик задачи
            max_retries: Максимальное количество попыток
            
        Returns:
            ID задачи или None если не удалось добавить
        """
        if not self._initialized:
            self.initialize()
        
        selected_pool = self._select_pool(priority, pool_type)
        task = PrioritizedTask.create(
            payload=payload,
            priority=priority,
            pool_type=selected_pool,
            handler=handler,
            max_retries=max_retries
        )
        
        pool = self._pools[selected_pool]
        success = await pool.submit(task)
        
        if success:
            logger.debug(
                f"Dispatched task {task.task_id} to {selected_pool} "
                f"(priority={priority.name})"
            )
            return task.task_id
        else:
            logger.warning(f"Failed to dispatch task to {selected_pool}")
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику всех пулов."""
        return {
            "pools": {
                pool_type: pool.get_stats()
                for pool_type, pool in self._pools.items()
            },
            "total_pools": len(self._pools)
        }
    
    def get_pool(self, pool_type: str) -> Optional[WorkerPool]:
        """Возвращает пул по типу."""
        return self._pools.get(pool_type)
