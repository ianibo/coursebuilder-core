# Copyright 2013 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Core data model classes."""

__author__ = 'Pavel Simakov (psimakov@google.com)'

import logging
import appengine_config
from config import ConfigProperty
import counters
from counters import PerfCounter
from entities import BaseEntity
import transforms
import utils
from google.appengine.api import memcache
from google.appengine.api import namespace_manager
from google.appengine.api import users
from google.appengine.ext import db


# We want to use memcache for both objects that exist and do not exist in the
# datastore. If object exists we cache its instance, if object does not exist
# we cache this object below.
NO_OBJECT = {}

# The default amount of time to cache the items for in memcache.
DEFAULT_CACHE_TTL_SECS = 60 * 5

# Global memcache controls.
CAN_USE_MEMCACHE = ConfigProperty(
    'gcb_can_use_memcache', bool, (
        'Whether or not to cache various objects in memcache. For production '
        'this value should be on to enable maximum performance. For '
        'development this value should be off so you can see your changes to '
        'course content instantaneously.'),
    appengine_config.PRODUCTION_MODE)

# performance counters
CACHE_PUT = PerfCounter(
    'gcb-models-cache-put',
    'A number of times an object was put into memcache.')
CACHE_HIT = PerfCounter(
    'gcb-models-cache-hit',
    'A number of times an object was found in memcache.')
CACHE_MISS = PerfCounter(
    'gcb-models-cache-miss',
    'A number of times an object was not found in memcache.')
CACHE_DELETE = PerfCounter(
    'gcb-models-cache-delete',
    'A number of times an object was deleted from memcache.')


class MemcacheManager(object):
    """Class that consolidates all memcache operations."""

    @classmethod
    def get(cls, key, namespace=None):
        """Gets an item from memcache if memcache is enabled."""
        if not CAN_USE_MEMCACHE.value:
            return None
        if not namespace:
            namespace = appengine_config.DEFAULT_NAMESPACE_NAME
        value = memcache.get(key, namespace=namespace)

        # We store some objects in memcache that don't evaluate to True, but are
        # real objects, '{}' for example. Count a cache miss only in a case when
        # an object is None.
        if value != None:  # pylint: disable-msg=g-equals-none
            CACHE_HIT.inc()
        else:
            logging.info('Cache miss, key: %s. %s', key, Exception())
            CACHE_MISS.inc(context=key)
        return value

    @classmethod
    def set(cls, key, value, ttl=DEFAULT_CACHE_TTL_SECS, namespace=None):
        """Sets an item in memcache if memcache is enabled."""
        if CAN_USE_MEMCACHE.value:
            CACHE_PUT.inc()
            if not namespace:
                namespace = appengine_config.DEFAULT_NAMESPACE_NAME
            memcache.set(key, value, ttl, namespace=namespace)

    @classmethod
    def incr(cls, key, delta, namespace=None):
        """Incr an item in memcache if memcache is enabled."""
        if CAN_USE_MEMCACHE.value:
            if not namespace:
                namespace = appengine_config.DEFAULT_NAMESPACE_NAME
            memcache.incr(key, delta, namespace=namespace, initial_value=0)

    @classmethod
    def delete(cls, key, namespace=None):
        """Deletes an item from memcache if memcache is enabled."""
        if CAN_USE_MEMCACHE.value:
            CACHE_DELETE.inc()
            if not namespace:
                namespace = appengine_config.DEFAULT_NAMESPACE_NAME
            memcache.delete(key, namespace=namespace)


CAN_AGGREGATE_COUNTERS = ConfigProperty(
    'gcb_can_aggregate_counters', bool,
    'Whether or not to aggregate and record counter values in memcache. '
    'This allows you to see counter values aggregated across all frontend '
    'application instances. Without recording, you only see counter values '
    'for one frontend instance you are connected to right now. Enabling '
    'aggregation improves quality of performance metrics, but adds a small '
    'amount of latency to all your requests.',
    default_value=False)


def incr_counter_global_value(name, delta):
    if CAN_AGGREGATE_COUNTERS.value:
        MemcacheManager.incr('counter:' + name, delta)


def get_counter_global_value(name):
    if CAN_AGGREGATE_COUNTERS.value:
        return MemcacheManager.get('counter:' + name)
    else:
        return None

counters.get_counter_global_value = get_counter_global_value
counters.incr_counter_global_value = incr_counter_global_value


# Whether to record tag events in a database.
CAN_SHARE_STUDENT_PROFILE = ConfigProperty(
    'gcb_can_share_student_profile', bool, (
        'Whether or not to share student profile between different courses.'),
    False)


class PersonalProfile(BaseEntity):
    """Personal information not specific to any course instance."""

    email = db.StringProperty(indexed=False)
    legal_name = db.StringProperty(indexed=False)
    nick_name = db.StringProperty(indexed=False)
    date_of_birth = db.DateProperty(indexed=False)
    enrollment_info = db.TextProperty()

    @property
    def user_id(self):
        return self.key().name()


class PersonalProfileDTO(object):
    """DTO for PersonalProfile."""

    def __init__(self, personal_profile=None):
        self.enrollment_info = '{}'
        if personal_profile:
            self.user_id = personal_profile.user_id
            self.email = personal_profile.email
            self.legal_name = personal_profile.legal_name
            self.nick_name = personal_profile.nick_name
            self.date_of_birth = personal_profile.date_of_birth
            self.enrollment_info = personal_profile.enrollment_info


class StudentProfileDAO(object):
    """All access and mutation methods for PersonalProfile and Student."""

    TARGET_NAMESPACE = appengine_config.DEFAULT_NAMESPACE_NAME

    @classmethod
    def _memcache_key(cls, key):
        """Makes a memcache key from primary key."""
        return 'entity:personal-profile:%s' % key

    @classmethod
    def _get_profile_by_user_id(cls, user_id):
        """Loads profile given a user_id and returns Entity object."""
        old_namespace = namespace_manager.get_namespace()
        try:
            namespace_manager.set_namespace(cls.TARGET_NAMESPACE)

            profile = MemcacheManager.get(
                cls._memcache_key(user_id), namespace=cls.TARGET_NAMESPACE)
            if profile == NO_OBJECT:
                return None
            if profile:
                return profile
            profile = PersonalProfile.get_by_key_name(user_id)
            MemcacheManager.set(
                cls._memcache_key(user_id), profile if profile else NO_OBJECT,
                namespace=cls.TARGET_NAMESPACE)
            return profile
        finally:
            namespace_manager.set_namespace(old_namespace)

    @classmethod
    def _add_new_profile(cls, user_id, email):
        """Adds new profile for a user_id and returns Entity object."""
        if not CAN_SHARE_STUDENT_PROFILE.value:
            return None

        old_namespace = namespace_manager.get_namespace()
        try:
            namespace_manager.set_namespace(cls.TARGET_NAMESPACE)

            profile = PersonalProfile(key_name=user_id)
            profile.email = email
            profile.enrollment_info = '{}'
            profile.put()
            return profile
        finally:
            namespace_manager.set_namespace(old_namespace)

    @classmethod
    def _update_attributes(
        cls, profile, student,
        email=None, legal_name=None, nick_name=None,
        date_of_birth=None, is_enrolled=None):
        """Modifies various attributes of Student and Profile."""

        # we allow profile to be null
        if not profile:
            profile = PersonalProfileDTO()

        # TODO(psimakov): update of email does not work for student
        if email is not None:
            profile.email = email

        if legal_name is not None:
            profile.legal_name = legal_name

        if nick_name is not None:
            profile.nick_name = nick_name
            student.name = nick_name

        if date_of_birth is not None:
            profile.date_of_birth = date_of_birth

        if is_enrolled is not None:
            from controllers import sites  # pylint: disable=C6204
            course = sites.get_course_for_current_request()
            enrollment_dict = transforms.loads(profile.enrollment_info)
            enrollment_dict[course.get_namespace_name()] = is_enrolled
            profile.enrollment_info = transforms.dumps(enrollment_dict)

            student.is_enrolled = is_enrolled

    @classmethod
    def _put_profile(cls, profile):
        """Does a put() on profile objects."""
        if not profile:
            return
        profile.put()
        MemcacheManager.delete(
            cls._memcache_key(profile.user_id),
            namespace=cls.TARGET_NAMESPACE)

    @classmethod
    @db.transactional(xg=True)
    def _update_in_transaction(
        cls, user_id,
        email, legal_name=None, nick_name=None,
        date_of_birth=None, is_enrolled=None):
        """Updates various Student and Profile attributes transactionally."""

        # load profile; it can be None
        profile = cls._get_profile_by_user_id(user_id)

        # load student
        student = Student.get_by_email(email)
        if not student:
            raise Exception('Unable to find student for: %s' % user_id)

        # mutate both
        cls._update_attributes(
            profile, student,
            email=email, legal_name=legal_name, nick_name=nick_name,
            date_of_birth=date_of_birth, is_enrolled=is_enrolled)

        # update both
        student.put()
        cls._put_profile(profile)

    @classmethod
    def get_profile_by_user_id(cls, user_id):
        """Loads profile given a user_id and returns DTO object."""
        profile = cls._get_profile_by_user_id(user_id)
        if profile:
            return PersonalProfileDTO(personal_profile=profile)
        return None

    @classmethod
    def add_new_profile(cls, user_id, email):
        return cls._add_new_profile(user_id, email)

    @classmethod
    def add_new_student_for_current_user(cls, nick_name, additional_fields):
        user = users.get_current_user()

        student_by_uid = Student.get_student_by_user_id(user.user_id())
        is_valid_student = (student_by_uid is None or
                            student_by_uid.user_id == user.user_id())
        assert is_valid_student, (
            'Student\'s email and user id do not match.')

        cls._add_new_student_for_current_user(
            user.user_id(), user.email(), nick_name, additional_fields)

    @classmethod
    @db.transactional(xg=True)
    def _add_new_student_for_current_user(
        cls, user_id, email, nick_name, additional_fields):
        """Create new or re-enroll old student."""

        # create profile if does not exist
        profile = cls._get_profile_by_user_id(user_id)
        if not profile:
            profile = cls._add_new_profile(user_id, email)

        # create new student or re-enroll existing
        student = Student.get_by_email(email)
        if not student:
            # TODO(psimakov): we must move to user_id as a key
            student = Student(key_name=email)

        # update profile
        cls._update_attributes(
            profile, student,
            nick_name=nick_name, is_enrolled=True)

        # update student
        student.user_id = user_id
        student.additional_fields = additional_fields

        # put both
        cls._put_profile(profile)
        student.put()

    @classmethod
    def update(
        cls, user_id, email,
        legal_name=None, nick_name=None, date_of_birth=None, is_enrolled=None):
        profile = cls.get_profile_by_user_id(user_id)
        if not profile:
            profile = cls.add_new_profile(user_id, email)
        cls._update_in_transaction(
            user_id, email=email,
            legal_name=legal_name, nick_name=nick_name,
            date_of_birth=date_of_birth, is_enrolled=is_enrolled)


class Student(BaseEntity):
    """Student data specific to a course instance."""
    enrolled_on = db.DateTimeProperty(auto_now_add=True, indexed=True)
    user_id = db.StringProperty(indexed=True)
    name = db.StringProperty(indexed=False)
    additional_fields = db.TextProperty(indexed=False)
    is_enrolled = db.BooleanProperty(indexed=False)

    # Each of the following is a string representation of a JSON dict.
    scores = db.TextProperty(indexed=False)

    @property
    def is_transient(self):
        return False

    @property
    def email(self):
        return self.key().name()

    @property
    def profile(self):
        return StudentProfileDAO.get_profile_by_user_id(self.user_id)

    @classmethod
    def _memcache_key(cls, key):
        """Makes a memcache key from primary key."""
        return 'entity:student:%s' % key

    def put(self):
        """Do the normal put() and also add the object to memcache."""
        result = super(Student, self).put()
        MemcacheManager.set(self._memcache_key(self.key().name()), self)
        return result

    def delete(self):
        """Do the normal delete() and also remove the object from memcache."""
        super(Student, self).delete()
        MemcacheManager.delete(self._memcache_key(self.key().name()))

    @classmethod
    def add_new_student_for_current_user(cls, nick_name, additional_fields):
        StudentProfileDAO.add_new_student_for_current_user(
            nick_name, additional_fields)

    @classmethod
    def get_by_email(cls, email):
        return Student.get_by_key_name(email.encode('utf8'))

    @classmethod
    def get_enrolled_student_by_email(cls, email):
        """Returns enrolled student or None."""
        student = MemcacheManager.get(cls._memcache_key(email))
        if NO_OBJECT == student:
            return None
        if not student:
            student = Student.get_by_email(email)
            if student:
                MemcacheManager.set(cls._memcache_key(email), student)
            else:
                MemcacheManager.set(cls._memcache_key(email), NO_OBJECT)
        if student and student.is_enrolled:
            return student
        else:
            return None

    @classmethod
    def _get_user_and_student(cls):
        """Loads user and student and asserts both are present."""
        user = users.get_current_user()
        if not user:
            raise Exception('No current user.')
        student = Student.get_by_email(user.email())
        if not student:
            raise Exception('Student instance corresponding to user %s not '
                            'found.' % user.email())
        return user, student

    @classmethod
    def rename_current(cls, new_name):
        """Gives student a new name."""
        _, student = cls._get_user_and_student()
        StudentProfileDAO.update(
            student.user_id, student.email, nick_name=new_name)

    @classmethod
    def set_enrollment_status_for_current(cls, is_enrolled):
        """Changes student enrollment status."""
        _, student = cls._get_user_and_student()
        StudentProfileDAO.update(
            student.user_id, student.email, is_enrolled=is_enrolled)

    def get_key(self):
        if not self.user_id:
            raise Exception('Student instance has no user_id set.')
        return db.Key.from_path(Student.kind(), self.user_id)

    @classmethod
    def get_student_by_user_id(cls, user_id):
        students = cls.all().filter(cls.user_id.name, user_id).fetch(limit=2)
        if len(students) == 2:
            raise Exception(
                'There is more than one student with user_id %s' % user_id)
        return students[0] if students else None

    def has_same_key_as(self, key):
        """Checks if the key of the student and the given key are equal."""
        return key == self.get_key()


class TransientStudent(object):
    """A transient student (i.e. a user who hasn't logged in or registered)."""

    @property
    def is_transient(self):
        return True


class EventEntity(BaseEntity):
    """Generic events.

    Each event has a 'source' that defines a place in a code where the event was
    recorded. Each event has a 'user_id' to represent an actor who triggered
    the event. The event 'data' is a JSON object, the format of which is defined
    elsewhere and depends on the type of the event.
    """
    recorded_on = db.DateTimeProperty(auto_now_add=True, indexed=True)
    source = db.StringProperty(indexed=False)
    user_id = db.StringProperty(indexed=False)

    # Each of the following is a string representation of a JSON dict.
    data = db.TextProperty(indexed=False)

    @classmethod
    def record(cls, source, user, data):
        """Records new event into a datastore."""

        event = EventEntity()
        event.source = source
        event.user_id = user.user_id()
        event.data = data
        event.put()


class StudentAnswersEntity(BaseEntity):
    """Student answers to the assessments."""

    updated_on = db.DateTimeProperty(indexed=True)

    # Each of the following is a string representation of a JSON dict.
    data = db.TextProperty(indexed=False)


class StudentPropertyEntity(BaseEntity):
    """A property of a student, keyed by the string STUDENT_ID-PROPERTY_NAME."""

    updated_on = db.DateTimeProperty(indexed=True)

    name = db.StringProperty()
    # Each of the following is a string representation of a JSON dict.
    value = db.TextProperty()

    @classmethod
    def _memcache_key(cls, key):
        """Makes a memcache key from primary key."""
        return 'entity:student_property:%s' % key

    @classmethod
    def create_key(cls, student_id, property_name):
        return '%s-%s' % (student_id, property_name)

    @classmethod
    def create(cls, student, property_name):
        return StudentPropertyEntity(
            key_name=cls.create_key(student.user_id, property_name),
            name=property_name)

    def put(self):
        """Do the normal put() and also add the object to memcache."""
        result = super(StudentPropertyEntity, self).put()
        MemcacheManager.set(self._memcache_key(self.key().name()), self)
        return result

    def delete(self):
        """Do the normal delete() and also remove the object from memcache."""
        super(Student, self).delete()
        MemcacheManager.delete(self._memcache_key(self.key().name()))

    @classmethod
    def get(cls, student, property_name):
        """Loads student property."""
        key = cls.create_key(student.user_id, property_name)
        value = MemcacheManager.get(cls._memcache_key(key))
        if NO_OBJECT == value:
            return None
        if not value:
            value = cls.get_by_key_name(key)
            if value:
                MemcacheManager.set(cls._memcache_key(key), value)
            else:
                MemcacheManager.set(cls._memcache_key(key), NO_OBJECT)
        return value


class QuestionEntity(BaseEntity):
    """An object representing a top-level question."""

    MULTIPLE_CHOICE = 0
    SHORT_ANSWER = 1

    type = db.IntegerProperty(
        indexed=False, choices=[MULTIPLE_CHOICE, SHORT_ANSWER])

    # A string representation of a JSON dict.
    data = db.TextProperty(indexed=False)

    @classmethod
    def _memcache_key(cls, question_id):
        """Makes a memcache key from datastore id."""
        return '(entity:question:%s)' % question_id

    def get_question_dict(self):
        return transforms.loads(self.data)

    def set_question_dict(self, question_dict):
        self.data = transforms.dumps(question_dict)

    @property
    def description(self):
        return self.get_question_dict().get('description')

    @property
    def id(self):
        return self.key().id()

    @classmethod
    def create(cls, question_type, question_dict):
        return QuestionEntity(
            type=question_type, data=transforms.dumps(question_dict))

    @classmethod
    def get_all_questions(cls):
        questions = []
        # pylint: disable=unnecessary-lambda
        utils.QueryMapper(cls.all(), batch_size=100).run(
            lambda item: questions.append(item))
        # pylint: enable=unnecessary-lambda
        return questions

    def put(self):
        """Do the normal put() and also add the object to memcache."""
        result = super(QuestionEntity, self).put()
        MemcacheManager.set(self._memcache_key(self.key().id()), self)
        return result

    def delete(self):
        """Do the normal delete() and also remove the object from memcache."""
        super(QuestionEntity, self).delete()
        MemcacheManager.delete(self._memcache_key(self.key().id()))

    @classmethod
    def find_question_by_id(cls, question_id):
        """Load the question from memcache or datastore."""
        question = MemcacheManager.get(cls._memcache_key(question_id))
        if NO_OBJECT == question:
            return None
        if not question:
            question = QuestionEntity.get_by_id(int(question_id))
            if question:
                MemcacheManager.set(cls._memcache_key(question_id), question)
            else:
                MemcacheManager.set(cls._memcache_key(question_id), NO_OBJECT)
        return question
