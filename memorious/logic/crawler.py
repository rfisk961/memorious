import os
import io
import yaml
import logging
import time
from datetime import timedelta, datetime

from memorious import settings
from memorious.core import session
from memorious.model import Tag, Operation, Result
from memorious.logic.context import handle
from memorious.logic.stage import CrawlerStage

log = logging.getLogger(__name__)


class Crawler(object):
    """A processing graph that constitutes a crawler."""
    SCHEDULES = {
        'daily': timedelta(days=1),
        'weekly': timedelta(weeks=1),
        'monthly': timedelta(weeks=4)
    }

    def __init__(self, manager, source_file):
        self.manager = manager
        self.source_file = source_file
        with io.open(source_file, encoding='utf-8') as fh:
            self.config_yaml = fh.read()
            self.config = yaml.load(self.config_yaml)

        self.name = os.path.basename(source_file)
        self.name = self.config.get('name', self.name)
        self.description = self.config.get('description', self.name)
        self.category = self.config.get('category', 'scrape')
        self.schedule = self.config.get('schedule')
        self.disabled = self.config.get('disabled', False)
        self.init_stage = self.config.get('init', 'init')
        self.delta = Crawler.SCHEDULES.get(self.schedule)
        self.delay = int(self.config.get('delay', 0))
        self.expire = int(self.config.get('expire', settings.EXPIRE))
        self.stealthy = self.config.get('stealthy', False)

        self.stages = {}
        for name, stage in self.config.get('pipeline', {}).items():
            self.stages[name] = CrawlerStage(self, name, stage)

    def check_due(self):
        """Check if the last execution of this crawler is older than
        the scheduled interval."""
        if self.disabled:
            return False
        if self.delta is None:
            return False
        last_run = Operation.last_run(self.name)
        if last_run is None:
            return True
        now = datetime.now()
        if now > last_run + self.delta:
            return True
        return False

    def flush(self):
        """Delete all run-time data generated by this crawler."""
        Tag.delete(self.name)
        Operation.delete(self.name)
        session.commit()

    def run(self, incremental=None):
        """Queue the execution of a particular crawler."""
        state = {
            'crawler': self.name,
            'incremental': settings.INCREMENTAL
        }
        if incremental is not None:
            state['incremental'] = incremental
        stage = self.get(self.init_stage)
        handle.delay(state, stage.name, {})
        if settings.EAGER:
            # If running in eager mode, we need to block until all the queued
            # tasks are finished.
            from memorious.core import task_queue
            while not task_queue.is_empty:
                time.sleep(1)

    def replay(self, stage):
        """Re-run all tasks issued to a particular stage.

        This sort of requires a degree of idempotence for each operation.
        Usually used to re-parse a set of crawled documents.
        """
        query = Result.by_crawler_next_stage(self.name, stage)
        for result in query:
            state = {'crawler': self.name}
            handle.delay(state, stage, result.data)

    def get(self, name):
        return self.stages.get(name)

    def __iter__(self):
        return iter(self.stages.values())

    def __repr__(self):
        return '<Crawler(%s)>' % self.name
