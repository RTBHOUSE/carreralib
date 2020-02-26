from __future__ import unicode_literals

import contextlib
import curses
import errno
import select
from datetime import datetime
import logging
import time
from typing import List

from google.cloud import datastore
from google.oauth2 import service_account

from . import ControlUnit

DATASTORE_CERT_PATH = './bigdatatech-warsaw-challenge-219525419ec7.json'
DATASTORE_ENTITY_NAME = 'race_results'
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
        self.last_lap_time = None
        self.best_lap_time = None
        self.laps = []

    def reset(self):
        self.time = None
        self.last_lap_time = None
        self.best_lap_time = None
        self.laps = []

    @property
    def finished_laps(self):
        return len(self.laps)

    def newlap(self, timer):
        if self.time is not None:
            self.last_lap_time = timer.timestamp - self.time
            self.laps.append(self.last_lap_time)
            if self.best_lap_time is None or self.last_lap_time < self.best_lap_time:
                self.best_lap_time = self.last_lap_time
                self.save_best_lap()
        self.time = timer.timestamp

    def save_best_lap(self):
        if self.name and self.best_lap_time:
            with open(RESULTS_CSV_FILE, 'a+') as file:
                file.write(f'{self.name}, {self.best_lap_time, datetime.utcnow()}\n')
            try:
                save_to_datastore(self)
            except BaseException as e:
                logging.warning('Failed to save to DataStore', exc_info=e)

    def __str__(self):
        return f'{self.name} | {self.finished_laps} | {formattime(self.time)} ' \
               f'| {formattime(self.last_lap_time)} | {formattime(self.best_lap_time)}'


def posgetter(driver: Driver):
    return (driver.best_lap_time or 10000000, -driver.finished_laps)


class RaceRunner:
    HEADER = 'Pos Name       Lap time  Best lap Laps Finished'
    FORMAT = '{pos:<4}{car:<8}{laptime:>10}{bestlap:>10} {laps:>5}'

    FOOTER = ' * * * * *  SPACE to start/restart, ESC quit'

    def __init__(self, control_unit: ControlUnit, window, drivers: List[Driver]):
        self.control_unit = control_unit
        self.status = None
        self.start = None
        self.drivers = drivers
        self.max_lap = 0

        self.window = window
        self.titleattr = curses.A_STANDOUT
        self.lightattr = curses.color_pair(1)
        self.reset()

    def reset(self):
        for driver in drivers:
            driver.reset()

        status = self.control_unit.request()
        while not isinstance(status, ControlUnit.Status):
            status = self.control_unit.request()
        self.status = status

        self.control_unit.reset()
        time.sleep(1)

    def run(self):
        self.window.nodelay(1)
        last = None

        while True:
            try:
                self.update()
                c = self.window.getch()

                if c == 27:  # ESC
                    break
                elif c == ord('r'):
                    self.reset()
                elif c == ord(' '):
                    self.reset()
                    self.control_unit.start()

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
                last = data

            except select.error as e:
                pass
            except IOError as e:
                if e.errno != errno.EINTR:
                    raise

    def handle_status(self, status):
        self.status = status

    def handle_timer(self, timer):
        if timer.address > 1:
            return

        if self.start is None:
            self.start = timer.timestamp

        logging.debug(f'handle_timer {timer}')
        driver = self.drivers[timer.address]
        driver.newlap(timer)
        self.max_lap = max(self.max_lap, driver.finished_laps)

    def update(self, blink=lambda: (time.time() * 2) % 2 == 0):
        window = self.window
        window.clear()
        nlines, ncols = window.getmaxyx()
        window.addnstr(0, 0, self.HEADER.ljust(ncols), ncols, self.titleattr)
        window.addnstr(nlines - 1, 0, self.FOOTER, ncols - 1)

        start = self.status.start
        if start == 0 or start == 7:
            pass
        elif start == 1:
            window.chgat(nlines - 1, 0, 2 * 5, self.lightattr)
        elif start < 7:
            window.chgat(nlines - 1, 0, 2 * (start - 1), self.lightattr)
        elif int(time.time() * 2) % 2 == 0:  # A_BLINK may not be supported
            window.chgat(nlines - 1, 0, 2 * 5, self.lightattr)

        for pos, driver in enumerate(sorted(self.drivers, key=posgetter, reverse=True), start=1):
            text = self.FORMAT.format(
                pos=pos, car=driver.name, time=driver.best_lap_time or '-', laps=driver.finished_laps,
                laptime=formattime(driver.last_lap_time),
                bestlap=formattime(driver.best_lap_time),
            )
            window.addnstr(pos, 0, text, ncols)
        window.refresh()


def save_to_datastore(driver: Driver):
    credentials = service_account.Credentials.from_service_account_file(DATASTORE_CERT_PATH)
    client = datastore.Client(project=credentials.project_id, credentials=credentials)

    entity = datastore.Entity(client.key(DATASTORE_ENTITY_NAME))
    entity['username'] = driver.name
    entity['best_lap'] = driver.best_lap_time
    entity['finished_at'] = datetime.utcnow()
    client.put(entity)


logging.basicConfig(level=logging.INFO,
                    filename=LOG_FILE_NAME,
                    format='%(message)s')

driver_name_1 = input('Name (yellow pad): ')
driver_name_2 = input('Name (blue pad): ')


with contextlib.closing(ControlUnit(DEVICE, timeout=1)) as control_unit:
    control_unit.version()

    drivers = [
        Driver(driver_name_1),
        Driver(driver_name_2)
    ]

    def run(window):
        curses.curs_set(0)
        curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
        runner = RaceRunner(control_unit, window, drivers)
        runner.run()

    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
    finally:
        control_unit.reset()
