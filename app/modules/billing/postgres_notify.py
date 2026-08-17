"""
PostgreSQL LISTEN/NOTIFY для wake-up сигналов
Механизм асинхронных уведомлений между компонентами биллинга.
"""
import asyncio
import logging
from typing import Callable, Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime
import json
import uuid

try:
    import asyncpg
except ImportError:
    asyncpg = None

logger = logging.getLogger(__name__)


@dataclass
class Notification:
    """Уведомление от PostgreSQL."""
    channel: str
    payload: str
    received_at: datetime
    notification_id: str = None
    
    def __post_init__(self):
        if self.notification_id is None:
            self.notification_id = str(uuid.uuid4())
    
    def parse_payload(self) -> Dict[str, Any]:
        """Парсинг JSON payload."""
        try:
            return json.loads(self.payload)
        except json.JSONDecodeError:
            return {"raw": self.payload}


class ChannelSubscription:
    """Подписка на канал уведомлений."""
    
    def __init__(
        self, 
        channel: str, 
        callback: Callable[[Notification], Any],
        pattern: Optional[str] = None
    ):
        self.channel = channel
        self.callback = callback
        self.pattern = pattern  # Для wildcard подписок
        self.active = True
        self.created_at = datetime.utcnow()


class PostgresNotifyListener:
    """
    Слушатель уведомлений PostgreSQL LISTEN/NOTIFY.
    Обрабатывает wake-up сигналы для синхронизации.
    """
    
    def __init__(
        self,
        dsn: str,
        channels: List[str] = None,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 10
    ):
        self.dsn = dsn
        self.channels = channels or []
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts
        
        self._connection: Optional[asyncpg.Connection] = None
        self._subscriptions: Dict[str, ChannelSubscription] = {}
        self._running = False
        self._reconnect_attempts = 0
        self._listener_task: Optional[asyncio.Task] = None
        self._stats = {
            'notifications_received': 0,
            'notifications_processed': 0,
            'errors': 0,
            'reconnects': 0
        }
    
    async def connect(self):
        """Подключение к PostgreSQL."""
        if asyncpg is None:
            raise ImportError("asyncpg is not installed. Run: pip install asyncpg")
        
        self._connection = await asyncpg.connect(self.dsn)
        logger.info("Connected to PostgreSQL for LISTEN/NOTIFY")
        
        # Подписка на каналы
        for channel in self.channels:
            await self._connection.add_listener(channel, self._on_notification)
            logger.info(f"Subscribed to channel: {channel}")
        
        self._reconnect_attempts = 0
    
    async def disconnect(self):
        """Отключение от PostgreSQL."""
        self._running = False
        
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        
        if self._connection:
            # Отписка от всех каналов
            for channel in self.channels:
                try:
                    await self._connection.remove_listener(channel, self._on_notification)
                except Exception:
                    pass
            
            await self._connection.close()
            logger.info("Disconnected from PostgreSQL")
    
    async def start(self):
        """Запуск слушателя уведомлений."""
        await self.connect()
        self._running = True
        self._listener_task = asyncio.create_task(self._listen_loop())
        logger.info("PostgreSQL LISTEN/NOTIFY listener started")
    
    async def stop(self):
        """Остановка слушателя уведомлений."""
        await self.disconnect()
        logger.info("PostgreSQL LISTEN/NOTIFY listener stopped")
    
    async def _listen_loop(self):
        """Основной цикл прослушивания уведомлений."""
        while self._running:
            try:
                await asyncio.sleep(0.1)  # Yield control
                
                # Проверка соединения
                if not self._connection or self._connection.is_closed():
                    await self._reconnect()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Listen loop error: {str(e)}")
                self._stats['errors'] += 1
                await self._reconnect()
    
    async def _reconnect(self):
        """Переподключение к PostgreSQL."""
        if self._reconnect_attempts >= self.max_reconnect_attempts:
            logger.error("Max reconnect attempts reached")
            raise ConnectionError("Failed to reconnect to PostgreSQL")
        
        self._reconnect_attempts += 1
        self._stats['reconnects'] += 1
        
        logger.info(f"Reconnecting to PostgreSQL (attempt {self._reconnect_attempts}/{self.max_reconnect_attempts})")
        await asyncio.sleep(self.reconnect_delay)
        
        try:
            await self.connect()
        except Exception as e:
            logger.error(f"Reconnection failed: {str(e)}")
            raise
    
    def _on_notification(self, connection, pid, channel, payload):
        """Обработчик входящих уведомлений."""
        notification = Notification(
            channel=channel,
            payload=payload,
            received_at=datetime.utcnow()
        )
        
        self._stats['notifications_received'] += 1
        logger.debug(f"Received notification on {channel}: {payload[:100]}")
        
        # Поиск подходящих подписок
        for sub_channel, subscription in self._subscriptions.items():
            if not subscription.active:
                continue
            
            # Проверка соответствия канала (с учетом wildcard)
            if self._match_channel(channel, sub_channel, subscription.pattern):
                asyncio.create_task(self._invoke_callback(subscription, notification))
    
    async def _invoke_callback(
        self, 
        subscription: ChannelSubscription, 
        notification: Notification
    ):
        """Вызов callback обработчика."""
        try:
            if asyncio.iscoroutinefunction(subscription.callback):
                await subscription.callback(notification)
            else:
                subscription.callback(notification)
            
            self._stats['notifications_processed'] += 1
            
        except Exception as e:
            logger.error(f"Callback error for channel {subscription.channel}: {str(e)}")
            self._stats['errors'] += 1
    
    def _match_channel(
        self, 
        actual_channel: str, 
        sub_channel: str, 
        pattern: Optional[str]
    ) -> bool:
        """Проверка соответствия канала подписке."""
        if actual_channel == sub_channel:
            return True
        
        if pattern:
            import fnmatch
            return fnmatch.fnmatch(actual_channel, pattern)
        
        return False
    
    def subscribe(
        self,
        channel: str,
        callback: Callable[[Notification], Any],
        pattern: Optional[str] = None
    ) -> str:
        """
        Подписка на канал уведомлений.
        Возвращает ID подписки.
        """
        subscription_id = str(uuid.uuid4())
        subscription = ChannelSubscription(channel, callback, pattern)
        self._subscriptions[subscription_id] = subscription
        logger.info(f"Subscribed to channel {channel} (pattern: {pattern})")
        return subscription_id
    
    def unsubscribe(self, subscription_id: str):
        """Отписка от канала."""
        subscription = self._subscriptions.pop(subscription_id, None)
        if subscription:
            subscription.active = False
            logger.info(f"Unsubscribed from channel {subscription.channel}")
    
    async def notify(
        self,
        channel: str,
        payload: Any,
        timeout: float = 5.0
    ):
        """
        Отправка уведомления в канал.
        Использует PostgreSQL NOTIFY.
        """
        if not self._connection or self._connection.is_closed():
            raise ConnectionError("Not connected to PostgreSQL")
        
        if isinstance(payload, (dict, list)):
            payload_str = json.dumps(payload)
        else:
            payload_str = str(payload)
        
        # PostgreSQL NOTIFY имеет ограничение 8000 байт на payload
        if len(payload_str) > 7900:
            logger.warning("Payload truncated due to PostgreSQL NOTIFY limit")
            payload_str = payload_str[:7900]
        
        query = f"SELECT pg_notify('{channel}', $1)"
        await self._connection.execute(query, payload_str)
        logger.debug(f"Sent notification to {channel}: {payload_str[:100]}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики."""
        return {
            **self._stats,
            'active_subscriptions': len([s for s in self._subscriptions.values() if s.active]),
            'connected': self._connection is not None and not self._connection.is_closed()
        }


# Глобальный экземпляр (ленивая инициализация)
_listener: Optional[PostgresNotifyListener] = None


def get_listener() -> Optional[PostgresNotifyListener]:
    """Получение глобального экземпляра слушателя."""
    return _listener


def init_listener(
    dsn: str,
    channels: List[str] = None
) -> PostgresNotifyListener:
    """Инициализация глобального слушателя."""
    global _listener
    
    if channels is None:
        channels = [
            'billing_sync',
            'billing_events',
            'billing_wakeup',
            'subscription_changes'
        ]
    
    _listener = PostgresNotifyListener(dsn, channels)
    logger.info(f"Initialized billing listener with channels: {channels}")
    return _listener


async def start_listener():
    """Запуск глобального слушателя."""
    if _listener:
        await _listener.start()


async def stop_listener():
    """Остановка глобального слушателя."""
    if _listener:
        await _listener.stop()


# Утилиты для работы с уведомлениями

class BillingEventTypes:
    """Типы событий биллинга."""
    QUOTE_CREATED = "quote.created"
    QUOTE_ACCEPTED = "quote.accepted"
    ORDER_CREATED = "order.created"
    ORDER_PAID = "order.paid"
    ORDER_CANCELLED = "order.cancelled"
    PAYMENT_SUCCESS = "payment.success"
    PAYMENT_FAILED = "payment.failed"
    SUBSCRIPTION_ACTIVATED = "subscription.activated"
    SUBSCRIPTION_UPDATED = "subscription.updated"
    SUBSCRIPTION_CANCELLED = "subscription.cancelled"
    FISCAL_DOCUMENT_CREATED = "fiscal_document.created"


def create_event_payload(
    event_type: str,
    entity_type: str,
    entity_id: str,
    data: Dict[str, Any],
    timestamp: datetime = None
) -> Dict[str, Any]:
    """Создание payload для события."""
    return {
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "data": data,
        "timestamp": (timestamp or datetime.utcnow()).isoformat(),
        "version": 1
    }


async def emit_billing_event(
    event_type: str,
    entity_type: str,
    entity_id: str,
    data: Dict[str, Any],
    channel: str = "billing_events"
):
    """
    Отправка события биллинга через PostgreSQL NOTIFY.
    """
    if not _listener:
        logger.warning("Listener not initialized, skipping event emission")
        return
    
    payload = create_event_payload(event_type, entity_type, entity_id, data)
    await _listener.notify(channel, payload)
    logger.info(f"Emitted billing event: {event_type} for {entity_type}:{entity_id}")
