from __future__ import unicode_literals

import argparse
import contextlib
import curses
import errno
import logging
import select
import time
from lib2to3.pgen2 import driver

from . import ControlUnit
from curses.textpad import Textbox, rectangle


def posgetter(driver):
    return (-driver.laps, driver.time)


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


class RMS(object):

    MAX_LAPS = 3

    HEADER = 'Pos No         Time  Lap time  Best lap Laps Finished'
    FORMAT = ('{pos:<4}#{car:<2}{time:>12}{laptime:>10}{bestlap:>10} {laps:>5} {finished}')

    FOOTER = ' * * * * *  SPACE to start/restart, ESC quit'

    FUEL_MASK = ControlUnit.Status.PIT_LANE_MODE

    class Driver(object):
        def __init__(self, num):
            self.num = num
            self.time = None
            self.laptime = None
            self.bestlap = None
            self.laps = 0
            self.finished = False

        def newlap(self, timer):
            if self.time is not None:
                self.laptime = timer.timestamp - self.time
                if self.bestlap is None or self.laptime < self.bestlap:
                    self.bestlap = self.laptime
                self.laps += 1
            self.time = timer.timestamp

    def __init__(self, cu, window):
        self.cu = cu
        self.window = window
        self.titleattr = curses.A_STANDOUT
        self.lightattr = curses.color_pair(1)
        self.reset()

    def reset(self):
        self.drivers = [self.Driver(num) for num in range(1, 9)]
        self.start = None
        # discard remaining timer messages
        status = self.cu.request()
        while not isinstance(status, ControlUnit.Status):
            status = self.cu.request()
        self.status = status
        # reset cu timer
        self.cu.reset()

    def run(self):
        self.window.nodelay(1)
        last = None
        while True:
            try:
                self.update()
                c = self.window.getch()
                if c == 27: # ESC
                    break
                elif c == ord(' '):
                    self.reset()
                    self.cu.start()
                elif c == ord('n'):
                    self.window.addstr(1, 0, "Enter the name of the 1st driver")

                    # curses.newwin(height, width, begin_y, begin_x)
                    editwin = curses.newwin(5, 30, 3, 1)
                    # rectangle(win, uly, ulx, lry, lrx)
                    # rectangle(self.window, 2, 0, 4, 1 + 30 + 1)
                    rectangle(self.window, 2, 0, 8, 32)
                    self.window.refresh()

                    box = Textbox(editwin)

                    # Let the user edit until Ctrl-G is struck.
                    box.edit()

                    # Get resulting contents
                    message = box.gather()
                data = self.cu.request()
                # prevent counting duplicate laps
                if data == last:
                    continue
                elif isinstance(data, ControlUnit.Status):
                    self.handle_status(data)
                elif isinstance(data, ControlUnit.Timer):
                    self.handle_timer(data)
                else:
                    logging.warn('Unknown data from CU: ' + data)
                last = data
            except select.error as e:
                pass
            except IOError as e:
                if e.errno != errno.EINTR:
                    raise

    def handle_status(self, status):
        self.status = status

    def handle_timer(self, timer):
        driver = self.drivers[timer.address]
        if driver.finished:
            return
        driver.newlap(timer)
        if self.start is None:
            self.start = timer.timestamp
        if driver.laps == self.MAX_LAPS:
            driver.finished = True
            logging.info("DRIVER: " + str(driver.num) + " " + str(driver.laptime))

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

        drivers = [driver for driver in self.drivers if driver.time]
        for pos, driver in enumerate(sorted(drivers, key=posgetter), start=1):
            if pos == 1:
                leader = driver
                t = formattime(driver.time - self.start, True)
            elif driver.laps == leader.laps:
                t = '+%ss' % formattime(driver.time - leader.time)
            else:
                gap = leader.laps - driver.laps
                t = '+%d Lap%s' % (gap, 's' if gap != 1 else '')
            text = self.FORMAT.format(
                pos=pos, car=driver.num, time=t, laps=driver.laps,
                laptime=formattime(driver.laptime),
                bestlap=formattime(driver.bestlap),
                finished='FINISHED' if driver.finished else ''
            )
            window.addnstr(pos, 0, text, ncols)
        window.refresh()


parser = argparse.ArgumentParser(prog='python -m carreralib')
parser.add_argument('device', metavar='DEVICE')
parser.add_argument('-l', '--logfile', default='carreralib.log')
parser.add_argument('-t', '--timeout', default=1.0, type=float)
parser.add_argument('-v', '--verbose', action='store_true')
args = parser.parse_args()

logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                    filename=args.logfile,
                    fmt='%(message)s')

with contextlib.closing(ControlUnit(args.device, timeout=args.timeout)) as cu:
    print('CU version %s' % cu.version())

    def run(win):
        curses.curs_set(0)
        curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
        rms = RMS(cu, win)
        rms.run()
    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
