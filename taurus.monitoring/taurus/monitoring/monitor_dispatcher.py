# ----------------------------------------------------------------------
# Numenta Platform for Intelligent Computing (NuPIC)
# Copyright (C) 2015, Numenta, Inc.  Unless you have purchased from
# Numenta, Inc. a separate commercial license for this software code, the
# following terms and conditions apply:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see http://www.gnu.org/licenses.
#
# http://numenta.org/licenses/
# ----------------------------------------------------------------------
from abc import ABCMeta, abstractmethod
from datetime import datetime, timedelta
from functools import wraps
import hashlib
import logging
import random
import sys
import traceback

from sqlalchemy import (BINARY,
                        String,
                        Text,
                        Column,
                        Index,
                        DATETIME,
                        Table)
from sqlalchemy.exc import IntegrityError

from taurus.monitoring import monitorsdb
from taurus.monitoring.monitorsdb.schema import metadata



g_logger = logging.getLogger(__name__)



NOTIFICATION_RETENTION_PERIOD = 7 # Number of days to retain notifications,
                                  # during which duplicates are not sent.
                                  # Duplicate problems that are not resolved
                                  # within the retention period will continue
                                  # to result in notifications no more frequent
                                  # than the retention period.



#pylint: disable=R0921
class MonitorDispatcher(object):
  __metaclass__ = ABCMeta

  _checks = []

  _dispatchTable = Table("monitor_dispatcher",
                         metadata,
                         Column("checkFn",
                                String(length=80),
                                nullable=False,
                                primary_key=True),
                         Column("excType",
                                String(length=80),
                                nullable=False,
                                primary_key=True),
                         Column("excValueDigest",
                                BINARY(length=20),
                                primary_key=True),
                         Column("timestamp",
                                DATETIME,
                                nullable=False),
                         Column("excValue",
                                Text()),
                         Index("timestamp_index",
                               "timestamp"))


  @abstractmethod
  def dispatchNotification(self, checkFn, excType, excValue, excTraceback):
    """ Send notification.  Implementation to be provided by subclass.

    :param function checkFn: The check function that raised an exception
    :param type excType: Exception type
    :param exception excValue: Exception value
    :param traceback excTraceback: Exception traceback
    :returns: None
    """
    raise NotImplementedError("dispatchNotification() must be implemented in "
                              "subclass per abc protocol.")


  @staticmethod
  def hashExceptionValue(excValue):
    """
    :param exception excValue:
    :returns: Hash digest (currently sha1)
    """
    return hashlib.sha1(str(excValue)).digest()



  @classmethod
  def clearAllNotificationsInteractiveConsoleScriptEntryPoint(cls):
    """ Interactive utility for manually clearing out all notifications.
    Meant to be used as a console script entry point, defined in setup.py.

    User will be prompted with a stern warning to delete notifications, and
    required to enter "Yes-" followed by a random integer.
    """
    engine = monitorsdb.engineFactory()
    expectedAnswer = "Yes-%s" % (random.randint(1, 30),)

    answer = raw_input(
      "Attention!  You are about to do something irreversible, and potentially"
      " dangerous.\n"
      "\n"
      "To back out immediately without making any changes, feel free to type "
      "anything but \"{}\" in the prompt below, and press return.\n"
      "\n"
      "Should you choose to continue, all notifications in the DATABASE \"{}\""
      "will be PERMANENTLY DELETED.\n"
      "\n"
      "Are you sure you want to continue? "
      .format(expectedAnswer, engine))

    if answer.strip() != expectedAnswer:
      print "Aborting - Wise choice, my friend. Bye."

    #pylint: disable=E1120
    cmd = cls._dispatchTable.delete()
    monitorsdb.retryOnTransientErrors(monitorsdb.engineFactory().execute)(cmd)

    print "Notifications deleted."



  @classmethod
  @monitorsdb.retryOnTransientErrors
  def cleanupOldNotifications(cls):
    """ Delete all notifications older than a date determined by subtracting
    the number of days held in the value of the NOTIFICATION_RETENTION_PERIOD
    module-level variable from the current UTC timestamp.
    """
    cutoffDate = (datetime.utcnow() -
                  timedelta(days=NOTIFICATION_RETENTION_PERIOD))
    #pylint: disable=E1120
    cmd = (cls._dispatchTable
              .delete()
              .where(cls._dispatchTable.c.timestamp < cutoffDate))
    monitorsdb.engineFactory().execute(cmd)


  @classmethod
  def recordNotification(cls, conn, checkFn, excType, excValue):
    """ Record notification, uniquely identified by the name of the function,
    the exception type, and a hash digest of the exception value that triggered
    the notification.  Duplicate notifications will be silently ignored.

    :param conn: Database connection.  Note, preventDuplicates() starts a
      transaction using the monitorsdb.engineFactory().begin() context
      manager.  If the actual dispatchNotification() call fails for whatever
      reason, the transaction is not completed and changes are not committed.
    :param function checkFn: Function that triggered notification
    :param excType: Exception type that triggered notification
    :param excValue: Actual exception that triggered notification
    :returns: Boolean result.  True if notification succesfully recorded, False
      if IntegrityError raised due to pre-existing duplicate.
    """
    excValueDigest = cls.hashExceptionValue(excValue)
    #pylint: disable=E1120
    ins = (cls._dispatchTable
              .insert()
              .values(checkFn=checkFn.__name__,
                      excType=excType.__name__,
                      excValueDigest=excValueDigest,
                      timestamp=datetime.utcnow(),
                      excValue=excValue))
    try:
      conn.execute(ins)
      return True
    except IntegrityError:
      g_logger.info("Duplicate notification quietly ignored -- {}"
                    .format(repr((checkFn, excType, excValue))))

    return False

  @classmethod
  def preventDuplicates(cls, dispatchNotification):
    """ Decorator to complement implementations of dispatchNotification to
    prevent similar errors from triggering multiple emails.

    :param function dispatchNotification: Implementation of
      dispatchNotification as required by abc protocol.  This function MUST
      implement the same signature as MonitorDispatcher.dispatchNotification()
    """
    @wraps(dispatchNotification)
    def wrappedDispatchNotification(
        self, checkFn, excType, excValue, excTraceback):
      """
      See dispatchNotification() for signature and docstring.
      """
      cls.cleanupOldNotifications()

      with monitorsdb.engineFactory().begin() as conn:
        """ Wrap dispatchNotification() in a transaction, in case there is an
        error that prevents the notification being dispatched.  In such a case
        we DO NOT want to save the notification so that it may be re-attempted
        later
        """
        if cls.recordNotification(conn, checkFn, excType, excValue):
          dispatchNotification(self, checkFn, excType, excValue, excTraceback)

    return wrappedDispatchNotification


  def checkAll(self):
    """ Run all previously-registered checks and send an email upon failure
    """
    for check in self._checks:
      try:
        check(self)
      except Exception: #pylint: disable=W0703
        self.dispatchNotification(check,
                                  sys.exc_type,
                                  sys.exc_value,
                                  sys.exc_traceback)


  @staticmethod
  def formatTraceback(excType, excValue, excTraceback):
    """ Helper utility to format an exception and traceback into a str.  Alias
    for::

      "".join(traceback.format_exception(excType, excValue, excTraceback))

    :returns: str
    """
    return "".join(traceback.format_exception(excType, excValue, excTraceback))


  @classmethod
  def registerCheck(cls, fn):
    """ Function decorator to register an externally defined function as a
    check.  Function must accept a ServerProxy instance as its first
    argument.
    """
    cls._checks.append(fn)
