import logging
import json
import pika
import functools
from twisted.internet import reactor
from scrapy.exceptions import DontCloseSpider, CloseSpider
from scrapy import signals
from rmq.connections import PikaSelectConnection
from rmq.utils import RMQConstants, RMQDefaultOptions
from rmq.items import RMQItem


logger = logging.getLogger(__name__)


class ItemProducerPipeline(object):
    _DEFAULT_HEARTBEAT = 300

    @classmethod
    def from_crawler(cls, crawler):
        o = cls(crawler)
        crawler.signals.connect(o.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(o.spider_closed, signal=signals.spider_closed)
        crawler.signals.connect(o.spider_idle, signal=signals.spider_idle)
        return o

    def __init__(self, crawler):
        super().__init__()
        self.crawler = crawler
        self.__spider = None

        self.delivery_tag_meta_key = RMQConstants.DELIVERY_TAG_META_KEY.value
        self.msg_body_meta_key = RMQConstants.MSG_BODY_META_KEY.value

        self.rmq_connection = None
        self._can_interact = False

        self.pending_items_buffer = []

    def spider_opened(self, spider):
        """execute on spider_opened signal and initialize connection, callbacks, start consuming"""
        """Set current spider instance"""
        self.__spider = spider

        """Check spider for correct declared callbacks/errbacks/methods/variables"""
        if self._validate_spider_has_attributes() is False:
            raise CloseSpider('Attached spider has no configured task_queue_name and processing_tasks observer')

        """Configure loggers"""
        logger.setLevel(self.__spider.settings.get("LOG_LEVEL", "INFO"))
        logging.getLogger("pika").setLevel(self.__spider.settings.get("PIKA_LOG_LEVEL", "WARNING"))

        """Declare/retrieve queue name from spider instance"""
        result_queue_name = spider.result_queue_name

        """Build pika connection parameters and start connection in separate twisted thread"""
        parameters = pika.ConnectionParameters(
            host=self.__spider.settings.get("RABBITMQ_HOST"),
            port=int(self.__spider.settings.get("RABBITMQ_PORT")),
            virtual_host=self.__spider.settings.get("RABBITMQ_VHOST"),
            credentials=pika.credentials.PlainCredentials(
                username=self.__spider.settings.get("RABBITMQ_USER"),
                password=self.__spider.settings.get("RABBITMQ_PASS"),
            ),
            heartbeat=RMQDefaultOptions.CONNECTION_HEARTBEAT.value,
        )
        reactor.callInThread(self.connect, parameters, result_queue_name)

    def spider_idle(self, spider):
        if len(self.pending_items_buffer):
            raise DontCloseSpider

    def spider_closed(self, spider):
        if self.rmq_connection is not None:
            while len(self.pending_items_buffer) and self._can_interact:
                self.send_message(self.pending_items_buffer.pop(0))
            if isinstance(self.rmq_connection.connection, pika.SelectConnection):
                self.rmq_connection.connection.ioloop.add_callback_threadsafe(self.rmq_connection.stop)

    def _validate_spider_has_attributes(self):
        spider_attributes = [attr for attr in dir(self.__spider) if not callable(getattr(self.__spider, attr))]
        if 'result_queue_name' not in spider_attributes:
            return False
        if not isinstance(self.__spider.result_queue_name, str) or len(self.__spider.result_queue_name) == 0:
            return False
        return True

    def set_connection_handle(self, connection):
        self.rmq_connection = connection
        self._can_interact = True

    def set_can_interact(self, can_interact):
        self._can_interact = can_interact

    def raise_close_spider(self):
        if self.crawler.engine.slot is None or self.crawler.engine.slot.closing:
            logger.critical('SPIDER ALREADY CLOSED')
            return
        self.crawler.engine.close_spider(self.__spider)

    def connect(self, parameters, queue_name):
        c = PikaSelectConnection(
            parameters,
            queue_name,
            owner=self,
            options={
                "enable_delivery_confirmations": False,
                "prefetch_count": self.__spider.settings.get("CONCURRENT_REQUESTS", 1),
            },
            is_consumer=False
        )
        c.run()

    def send_message(self, item):
        if isinstance(self.rmq_connection.connection, pika.SelectConnection):
            item_as_dictionary = dict(item)
            del item_as_dictionary[RMQConstants.DELIVERY_TAG_META_KEY.value]
            cb = functools.partial(self.rmq_connection.publish_message, message=json.dumps(item_as_dictionary))
            self.rmq_connection.connection.ioloop.add_callback_threadsafe(cb)

    def process_item(self, item, spider):
        if isinstance(item, RMQItem):
            if self._can_interact:
                while len(self.pending_items_buffer):
                    self.send_message(self.pending_items_buffer.pop(0))
                self.send_message(item)
            else:
                self.pending_items_buffer.append(item)
        return item
