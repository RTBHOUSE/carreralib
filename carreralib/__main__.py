from __future__ import unicode_literals

import contextlib
from datetime import datetime
import logging
import time
from typing import List

from google.cloud import datastore
from google.oauth2 import service_account

from . import ControlUnit

LOG_FILE_NAME = 'carreralib.log'
DEVICE = 'F8:69:3D:77:50:EA'
RESULTS_CSV_FILE = 'results.csv'
MAX_LAPS = 5


def formattime(time, longfmt=False):
    if time is None:
        return 'n/a'
    s = time // 1000
    ms = time % 1000

    if not longfmt:
        return '%d.%03d' % (s, ms)
    elif s < 3600:
        return '%d:%02d.%03d' % (s // 60, s % 60, ms)
    else:
        return '%d:%02d:%02d.%03d' % (s // 3600, (s // 60) % 60, s % 60, ms)


class Driver(object):
    def __init__(self, name):
        self.name = name
        self.time = None
        self.laptime = None
        self.bestlap = None
        self.laps = []
        self.finished_at = None

    @property
    def finished_laps(self):
        return len(self.laps)

    @property
    def finished(self):
        return self.finished_at is not None

    def newlap(self, timer):
        if self.time is not None:
            self.laptime = timer.timestamp - self.time
            self.laps.append(self.laptime)
            if self.bestlap is None or self.laptime < self.bestlap:
                self.bestlap = self.laptime
        self.time = timer.timestamp

    def __str__(self):
        return f'{self.name} | {self.finished_laps} | {formattime(self.time)} ' \
               f'| {formattime(self.laptime)} | {formattime(self.bestlap)} ' \
               f'| {self.finished_at}'


class RaceRunner:
    def __init__(self, control_unit: ControlUnit, drivers: List[Driver]):
        self.control_unit = control_unit
        self.status = None
        self.start = None
        self.drivers = drivers
        self.max_lap = 0

    def run(self):
        self.control_unit.reset()
        time.sleep(1)
        self.control_unit.clrpos()
        time.sleep(1)
        self.control_unit.start()
        time.sleep(1)
        self.control_unit.start()
        time.sleep(1)

        last = None

        while True:
            data = self.control_unit.request()
            if data == last:
                continue

            logging.debug(data)
            if isinstance(data, ControlUnit.Status):
                self.handle_status(data)
            elif isinstance(data, ControlUnit.Timer):
                self.handle_timer(data)
            else:
                logging.warning(f'Unknown data from ControlUnit: {data}')

            if any(map(lambda d: d.finished, self.drivers)):
                break
            last = data

    def handle_status(self, status):
        self.status = status

    def handle_timer(self, timer):
        if timer.address > 0:
            return

        logging.debug(f'handle_timer {timer}')
        driver = self.drivers[timer.address]
        if driver.finished:
            return
        driver.newlap(timer)
        if self.start is None:
            self.start = timer.timestamp
        if self.max_lap < driver.finished_laps:
            self.max_lap = driver.finished_laps
            self.control_unit.setlap(self.max_lap % 250)
        if driver.finished_laps == MAX_LAPS:
            driver.finished_at = datetime.utcnow()
            logging.info("DRIVER: " + driver.name + " " + str(driver.laptime))

        self.show_table()

    def show_table(self):
        print('-' * 8 + f'  {self.max_lap} ' + '-' * 8)
        for driver in self.drivers:
            print(driver)
        print('-' * 20)


def save_to_datastore(driver):
    credentials = service_account.Credentials \
        .from_service_account_file('./bigdatatech-warsaw-challenge-219525419ec7.json')
    client = datastore.Client(project=credentials.project_id, credentials=credentials)

    entity = datastore.Entity(client.key('race_results'))
    entity['username'] = driver.name
    entity['best_lap'] = driver.bestlap
    entity['finished_at'] = finished_at
    entity['laps'] = driver.laps
    client.put(entity)


logging.basicConfig(level=logging.DEBUG,
                    filename=LOG_FILE_NAME,
                    format='%(message)s')

driver_name = input('Name (yellow pad): ')

with contextlib.closing(ControlUnit(DEVICE, timeout=1)) as control_unit:
    print('CU version %s' % control_unit.version())

    driver = Driver(driver_name)

    try:
        runner = RaceRunner(control_unit, [driver])
        runner.run()

        finished_at = datetime.now()

        with open(RESULTS_CSV_FILE, 'a+') as file:
            file.write(f'{driver.name}, {driver.bestlap}, {finished_at}\n')

        save_to_datastore(driver)

    finally:
        control_unit.reset()
