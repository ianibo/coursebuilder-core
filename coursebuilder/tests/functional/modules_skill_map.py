# Copyright 2015 Google Inc. All Rights Reserved.
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

"""Tests for the skill mapping module."""

__author__ = 'John Orr (jorr@google.com)'

import cgi
import urllib

from networkx import DiGraph
from xml.etree import cElementTree

from common import crypto
from controllers import sites
from models import courses
from models import transforms
from modules.skill_map.skill_map import LESSON_SKILL_LIST_KEY
from modules.skill_map.skill_map import Skill
from modules.skill_map.skill_map import SkillGraph
from modules.skill_map.skill_map import SkillMap
from modules.skill_map.skill_map_metrics import SkillMapMetrics
from tests.functional import actions

from google.appengine.api import namespace_manager

ADMIN_EMAIL = 'admin@foo.com'
COURSE_NAME = 'skill_map_course'

SKILL_NAME = 'rock climbing'
SKILL_DESC = 'Knows how to climb rocks'

SKILL_NAME_2 = 'ice skating'
SKILL_DESC_2 = 'Knows how to ice skate'

SKILL_NAME_3 = 'skiing'
SKILL_DESC_3 = 'Knows how to ski'


class BaseSkillMapTests(actions.TestBase):

    def setUp(self):
        super(BaseSkillMapTests, self).setUp()

        self.base = '/' + COURSE_NAME
        context = actions.simple_add_course(
            COURSE_NAME, ADMIN_EMAIL, 'Skills Map Course')
        self.old_namespace = namespace_manager.get_namespace()
        namespace_manager.set_namespace('ns_%s' % COURSE_NAME)

        self.course = courses.Course(None, context)

    def tearDown(self):
        del sites.Registry.test_overrides[sites.GCB_COURSES_CONFIG.name]
        namespace_manager.set_namespace(self.old_namespace)
        super(BaseSkillMapTests, self).tearDown()

    def _build_sample_graph(self):
        # a
        #  \
        #   d
        #  /
        # b
        # c--e--f
        self.skill_graph = SkillGraph.load()
        self.sa = self.skill_graph.add(Skill.build('a', ''))
        self.sb = self.skill_graph.add(Skill.build('b', ''))
        self.sd = self.skill_graph.add(Skill.build('d', ''))
        self.skill_graph.add_prerequisite(self.sd.id, self.sa.id)
        self.skill_graph.add_prerequisite(self.sd.id, self.sb.id)

        self.sc = self.skill_graph.add(Skill.build('c', ''))
        self.se = self.skill_graph.add(Skill.build('e', ''))
        self.skill_graph.add_prerequisite(self.se.id, self.sc.id)

        self.sf = self.skill_graph.add(Skill.build('f', ''))
        self.skill_graph.add_prerequisite(self.sf.id, self.se.id)


class SkillGraphTests(BaseSkillMapTests):

    def test_add_skill(self):
        # Skill map is initially empty
        skill_graph = SkillGraph.load()
        self.assertEqual(0, len(skill_graph.skills))

        # Add a single skill
        skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        self.assertEqual(1, len(skill_graph.skills))

        # Retrieve the skill by id
        self.assertEqual(SKILL_NAME, skill.name)
        self.assertEqual(SKILL_DESC, skill.description)

    def test_add_skill_twice_is_rejected(self):
        skill_graph = SkillGraph.load()

        # Add a single skill
        skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        self.assertEqual(1, len(skill_graph.skills))

        # Retrieve the skill by id and add it again
        with self.assertRaises(AssertionError):
            skill_graph.add(skill)

    def test_delete_skill(self):
        # Skill map is initially empty
        skill_graph = SkillGraph.load()
        self.assertEqual(0, len(skill_graph.skills))

        # Add a single skill
        skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        self.assertEqual(1, len(skill_graph.skills))

        # Delete the skill and expect empty
        skill_graph.delete(skill.id)
        self.assertEqual(0, len(skill_graph.skills))

    def test_delete_skill_with_successors(self):
        skill_graph = SkillGraph.load()

        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        skill_2 = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))

        # Skill 1 is a prerequisite for Skill 2
        skill_graph.add_prerequisite(skill_2.id, skill_1.id)

        skill_graph.delete(skill_1.id)
        self.assertEqual(1, len(skill_graph.skills))
        self.assertEqual(skill_2, skill_graph.skills[0])
        self.assertEqual(0, len(skill_graph.prerequisites(skill_2.id)))

    def test_add_prerequisite(self):
        skill_graph = SkillGraph.load()

        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        skill_2 = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))

        # Skill 1 is a prerequisite for Skill 2
        skill_graph.add_prerequisite(skill_2.id, skill_1.id)

        skill_graph = SkillGraph.load()
        self.assertEqual(1, len(skill_graph.prerequisites(skill_2.id)))
        self.assertEqual(
            skill_1.id, skill_graph.prerequisites(skill_2.id)[0].id)

        self.assertEqual(1, len(skill_graph.successors(skill_1.id)))
        self.assertEqual(
            skill_2.id, skill_graph.successors(skill_1.id)[0].id)

    def test_add_missing_prerequisites_rejected(self):
        skill_graph = SkillGraph.load()

        with self.assertRaises(AssertionError):
            skill_graph.add_prerequisite('missing', 'also missing')

        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))

        with self.assertRaises(AssertionError):
            skill_graph.add_prerequisite('missing', skill_1.id)

        with self.assertRaises(AssertionError):
            skill_graph.add_prerequisite(skill_1.id, 'also missing')

    def test_add_loop_rejected(self):
        """Test that cannot add a skill with a length-1 cycle."""
        skill_graph = SkillGraph.load()

        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))

        with self.assertRaises(AssertionError):
            skill_graph.add_prerequisite(skill_1.id, skill_1.id)

    def test_add_duplicate_prerequisites_rejected(self):
        skill_graph = SkillGraph.load()

        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        skill_2 = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))

        skill_graph.add_prerequisite(skill_2.id, skill_1.id)
        with self.assertRaises(AssertionError):
            skill_graph.add_prerequisite(skill_2.id, skill_1.id)

    def test_delete_prerequisite(self):
        skill_graph = SkillGraph.load()

        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        skill_2 = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))
        skill_3 = skill_graph.add(Skill.build(SKILL_NAME_3, SKILL_DESC_3))

        # Skills 1 and 2 are prerequisites for Skill 3
        skill_graph.add_prerequisite(skill_3.id, skill_1.id)
        skill_graph.add_prerequisite(skill_3.id, skill_2.id)

        skill_graph = SkillGraph.load()
        self.assertEqual(2, len(skill_graph.prerequisites(skill_3.id)))

        # Delete skill 1 as a prerequisite and expect that only skill 2 is a
        # prerequisite now
        skill_graph.delete_prerequisite(skill_3.id, skill_1.id)

        self.assertEqual(1, len(skill_graph.prerequisites(skill_3.id)))
        self.assertEqual(
            skill_2.id, skill_graph.prerequisites(skill_3.id)[0].id)

    def test_delete_missing_prerequisites_rejected(self):
        skill_graph = SkillGraph.load()

        with self.assertRaises(AssertionError):
            skill_graph.delete_prerequisite('missing', 'also missing')

        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        skill_2 = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))

        with self.assertRaises(AssertionError):
            skill_graph.delete_prerequisite('missing', skill_1.id)

        with self.assertRaises(AssertionError):
            skill_graph.delete_prerequisite(skill_1.id, 'also missing')

        # Also reject deletion of a prerequisite if the skill exists but is not
        # currently a prerequisite
        with self.assertRaises(AssertionError):
            skill_graph.delete_prerequisite(skill_1.id, skill_2.id)

    def test_multiple_successors(self):
        skill_graph = SkillGraph.load()

        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        skill_2 = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))
        skill_3 = skill_graph.add(Skill.build(SKILL_NAME_3, SKILL_DESC_3))

        # Skills 2 and 3 are successors of Skill 1
        skill_graph.add_prerequisite(skill_2.id, skill_1.id)
        skill_graph.add_prerequisite(skill_3.id, skill_1.id)

        skill_graph = SkillGraph.load()
        successor_ids = {s.id for s in skill_graph.successors(skill_1.id)}
        self.assertEqual({skill_2.id, skill_3.id}, successor_ids)


class SkillMapTests(BaseSkillMapTests):

    def setUp(self):
        super(SkillMapTests, self).setUp()

        self.unit = self.course.add_unit()
        self.unit.title = 'Test Unit'
        self.lesson1 = self.course.add_lesson(self.unit)
        self.lesson1.title = 'Test Lesson 1'
        self.lesson2 = self.course.add_lesson(self.unit)
        self.lesson2.title = 'Test Lesson 2'
        self.lesson3 = self.course.add_lesson(self.unit)
        self.lesson3.title = 'Test Lesson 3'
        self.course.save()

    def tearDown(self):
        self.course.clear_current()
        super(SkillMapTests, self).tearDown()

    def test_topo_sort(self):
        self._build_sample_graph()
        skill_map = SkillMap.load(self.course)
        self.assertEqual(6, len(skill_map.skills()))
        # verify topological co-sets
        expected = {
            0: set([self.sa.id, self.sb.id, self.sc.id]),
            1: set([self.se.id, self.sd.id]),
            2: set([self.sf.id])}
        for ind, co_set in enumerate(skill_map._topo_sort()):
            self.assertEqual(expected[ind], co_set)

        # verify sorting skills by lesson
        expected = ['a', 'b', 'd', 'c', 'e', 'f']
        self.assertEqual(
            expected,
            [s.name for s in skill_map.skills(sort_by='lesson')])

        # verify sorting skills by name
        expected = ['a', 'b', 'c', 'd', 'e', 'f']
        self.assertEqual(
            expected,
            [s.name for s in skill_map.skills(sort_by='name')])

        # verify sorting skills by prerequisites
        expected = ['a', 'b', 'c', 'd', 'e', 'f']
        actual = [s.name
                  for s in skill_map.skills(sort_by='prerequisites')]
        self.assertEqual(expected, actual)

    def test_get_lessons_for_skill(self):
        skill_graph = SkillGraph.load()
        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        skill_2 = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))

        # lesson 1 has one skill
        self.lesson1.properties[LESSON_SKILL_LIST_KEY] = [skill_1.id]
        # lesson 2 has no skills
        # lesson 3 has both skills
        self.lesson3.properties[LESSON_SKILL_LIST_KEY] = [
                skill_1.id, skill_2.id]
        self.course.save()

        skill_map = SkillMap.load(self.course)

        lessons = skill_map.get_lessons_for_skill(skill_1)
        self.assertEqual(2, len(lessons))
        self.assertEqual(self.lesson1.lesson_id, lessons[0].lesson_id)
        self.assertEqual(self.lesson3.lesson_id, lessons[1].lesson_id)

        lessons = skill_map.get_lessons_for_skill(skill_2)
        self.assertEqual(1, len(lessons))
        self.assertEqual(self.lesson3.lesson_id, lessons[0].lesson_id)

    def test_get_lessons_returns_empty_list_when_no_skills_assigned(self):
        skill_graph = SkillGraph.load()
        skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))

        skill_map = SkillMap.load(self.course)

        # look up lessons by skill id
        lessons = skill_map.get_lessons_for_skill(skill)
        self.assertIsNotNone(lessons)
        self.assertEqual(0, len(lessons))

        # look up lessons by skill
        lessons = skill_map.get_lessons_for_skill(skill)
        self.assertIsNotNone(lessons)
        self.assertEqual(0, len(lessons))


class SkillListRestHandlerTests(BaseSkillMapTests):
    URL = 'rest/modules/skill_map/skill_list'

    def test_rejected_if_not_authorized(self):
        # Not logged in
        response = transforms.loads(self.get(self.URL).body)
        self.assertEqual(401, response['status'])

        # logged in but not admin
        actions.login('user.foo.com')
        response = transforms.loads(self.get(self.URL).body)
        self.assertEqual(401, response['status'])

        # logged in as admin
        actions.logout()
        actions.login(ADMIN_EMAIL)
        response = transforms.loads(self.get(self.URL).body)
        self.assertEqual(200, response['status'])

    def test_get_skill_list(self):
        skill_graph = SkillGraph.load()

        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        skill_2 = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))
        skill_3 = skill_graph.add(Skill.build(SKILL_NAME_3, SKILL_DESC_3))

        actions.login(ADMIN_EMAIL)
        response = transforms.loads(self.get(self.URL).body)

        self.assertEqual(200, response['status'])
        self.assertIn('xsrf_token', response)

        skill_list = transforms.loads(response['payload'])['skill_list']
        self.assertEqual(3, len(skill_list))

        expected_skill_list = [
            {
                'id': skill_1.id,
                'name': skill_1.name,
                'description': skill_1.description,
                'prerequisite_ids': []},
            {
                'id': skill_2.id,
                'name': skill_2.name,
                'description': skill_2.description,
                'prerequisite_ids': []},
            {
                'id': skill_3.id,
                'name': skill_3.name,
                'description': skill_3.description,
                'prerequisite_ids': []}]
        self.assertEqual(expected_skill_list, skill_list)


class SkillRestHandlerTests(BaseSkillMapTests):
    URL = 'rest/modules/skill_map/skill'
    XSRF_TOKEN = 'skill-handler'

    def _put(
            self, version=None, name=None, description=None,
            prerequisite_ids=None, xsrf_token=None, key=None):
        payload = {
            'version': version,
            'name': name,
            'description': description}
        if prerequisite_ids:
            payload['prerequisites'] = [
                {'id': pid} for pid in prerequisite_ids]
        request_dict = {
            'key': key,
            'xsrf_token': xsrf_token,
            'payload': transforms.dumps(payload)}
        response = self.put(
            self.URL, {'request': transforms.dumps(request_dict)})
        return transforms.loads(response.body)

    def test_rejected_if_not_authorized(self):
        # Bad XSRF_TOKEN
        response = self._put(
            version='1', name=SKILL_NAME, description=SKILL_DESC,
            xsrf_token='BAD XSRF TOKEN')
        self.assertEqual(403, response['status'])

        # Not logged in
        xsrf_token = crypto.XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN)
        response = self._put(
            version='1', name=SKILL_NAME, description=SKILL_DESC,
            xsrf_token=xsrf_token)
        self.assertEqual(401, response['status'])

        # Not admin
        actions.login('not-an-admin@foo.com')
        xsrf_token = crypto.XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN)
        response = self._put(
            version='1', name=SKILL_NAME, description=SKILL_DESC,
            xsrf_token=xsrf_token)
        self.assertEqual(401, response['status'])

    def test_create_skill(self):
        skill_graph = SkillGraph.load()
        self.assertEqual(0, len(skill_graph.skills))

        actions.login(ADMIN_EMAIL)
        xsrf_token = crypto.XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN)
        response = self._put(
            version='1', name=SKILL_NAME, description=SKILL_DESC,
            xsrf_token=xsrf_token)
        self.assertEqual(200, response['status'])
        self.assertEqual('Saved.', response['message'])

        payload = transforms.loads(response['payload'])
        key = payload['key']

        skill_graph = SkillGraph.load()
        self.assertEqual(1, len(skill_graph.skills))

        skill = skill_graph.get(key)
        self.assertEqual(key, skill.id)
        self.assertEqual(SKILL_NAME, skill.name)
        self.assertEqual(SKILL_DESC, skill.description)

    def test_create_skill_with_prerequisites(self):
        skill_graph = SkillGraph.load()

        src_skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))

        # add skill with one prerequisite
        actions.login(ADMIN_EMAIL)
        xsrf_token = crypto.XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN)
        response = self._put(
            version='1', name=SKILL_NAME_2,
            description=SKILL_DESC_2,
            prerequisite_ids=[src_skill.id],
            xsrf_token=xsrf_token)

        self.assertEqual(200, response['status'])
        self.assertEqual('Saved.', response['message'])
        payload = transforms.loads(response['payload'])

        tgt_key = payload['key']
        skill_graph = SkillGraph.load()
        self.assertEqual(2, len(skill_graph.skills))
        prerequisites = skill_graph.prerequisites(tgt_key)
        self.assertEqual(1, len(prerequisites))
        self.assertEqual(src_skill.id, prerequisites[0].id)

        tgt_skill = payload['skill']
        self.assertEqual(SKILL_NAME_2, tgt_skill['name'])
        self.assertEqual(tgt_skill['description'], SKILL_DESC_2)
        self.assertEqual([], tgt_skill['locations'])
        self.assertEqual(1, len(tgt_skill['prerequisites']))

        skills_list = payload['skills']
        self.assertEqual(2, len(skills_list))

    def test_update_prerequisites(self):
        skill_graph = SkillGraph.load()

        src_skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        tgt_skill = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))

        # update prerequisites
        actions.login(ADMIN_EMAIL)
        xsrf_token = crypto.XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN)
        response = self._put(
            version='1',
            name=SKILL_NAME_3,
            description=SKILL_DESC_3,
            prerequisite_ids=[src_skill.id],
            xsrf_token=xsrf_token,
            key=tgt_skill.id)

        self.assertEqual(200, response['status'])
        self.assertEqual('Saved.', response['message'])

        payload = transforms.loads(response['payload'])
        tgt_key = payload['key']

        skill_graph = SkillGraph.load()
        tgt_skill = skill_graph.get(tgt_key)

        self.assertEqual(2, len(skill_graph.skills))
        prerequisites = skill_graph.prerequisites(tgt_key)
        self.assertEqual(1, len(prerequisites))
        self.assertEqual(src_skill.id, prerequisites[0].id)
        self.assertEqual(tgt_skill.name, SKILL_NAME_3)
        self.assertEqual(tgt_skill.description, SKILL_DESC_3)

    def test_reject_update_with_duplicate_prerequisites(self):
        skill_graph = SkillGraph.load()

        src_skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        tgt_skill = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))

        actions.login(ADMIN_EMAIL)
        xsrf_token = crypto.XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN)
        response = self._put(
            version='1',
            name=SKILL_NAME_3,
            description=SKILL_DESC_3,
            prerequisite_ids=[src_skill.id, src_skill.id],
            xsrf_token=xsrf_token,
            key=tgt_skill.id)

        self.assertEqual(412, response['status'])
        self.assertEqual('Prerequisites must be unique', response['message'])

    def test_reject_update_prerequisites_with_self_loop(self):
        skill_graph = SkillGraph.load()
        skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        actions.login(ADMIN_EMAIL)
        xsrf_token = crypto.XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN)
        response = self._put(
            version='1',
            name=skill.name,
            description=skill.description,
            prerequisite_ids=[skill.id],
            xsrf_token=xsrf_token,
            key=skill.id)

        self.assertEqual(412, response['status'])
        self.assertEqual(
            'A skill cannot be its own prerequisite', response['message'])
        skill_graph = SkillGraph.load()
        skill = skill_graph.get(skill.id)
        self.assertEqual(set(), skill.prerequisite_ids)

    def test_get_skills(self):
        skill_graph = SkillGraph.load()

        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        skill_2 = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))
        skill_3 = skill_graph.add(Skill.build(SKILL_NAME_3, SKILL_DESC_3))
        skills = [skill_1, skill_2, skill_3]

        actions.login(ADMIN_EMAIL)
        response = transforms.loads(self.get(self.URL).body)

        self.assertEqual(200, response['status'])

        skill_list = transforms.loads(response['payload'])['skill_list']
        self.assertEqual(3, len(skill_list))
        for expected_skill in skills:
            skill = next(
                (x for x in skill_list if x['id'] == expected_skill.id), None)
            assert skill
            self.assertEqual(expected_skill.name, skill['name'])
            self.assertEqual(expected_skill.description, skill['description'])

    def test_get_skill(self):
        skill_graph = SkillGraph.load()
        skill_1 = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        actions.login(ADMIN_EMAIL)
        get_url = '%s?%s' % (self.URL, urllib.urlencode({'key': skill_1.id}))
        response = transforms.loads(self.get(get_url).body)
        self.assertEqual(200, response['status'])
        skill = transforms.loads(response['payload'])['skill']
        self.assertEqual(skill_1.id, skill['id'])
        self.assertEqual(skill_1.name, skill['name'])
        self.assertEqual(skill_1.description, skill['description'])

    def test_delete_skill(self):
        skill_graph = SkillGraph.load()
        skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))

        actions.login(ADMIN_EMAIL)
        xsrf_token = crypto.XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN)
        delete_url = '%s?%s' % (
            self.URL,
            urllib.urlencode({
                'key': skill.id,
                'xsrf_token': cgi.escape(xsrf_token)
            }))
        response = self.delete(delete_url)
        self.assertEquals(200, response.status_int)

    def test_delete_skill_with_lesson(self):
        # add a unit and a lesson to the course
        unit = self.course.add_unit()
        unit.title = 'Test Unit'
        lesson = self.course.add_lesson(unit)
        lesson.title = 'Test Lesson'
        self.course.save()

        # add one skill to the lesson
        skill_graph = SkillGraph.load()
        skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        lesson.properties[LESSON_SKILL_LIST_KEY] = [skill.id]
        self.course.update_lesson(lesson)
        self.course.save()

        skill_map = SkillMap.load(self.course)
        lessons = skill_map.get_lessons_for_skill(skill)
        self.assertEqual(1, len(lessons))
        self.assertEqual('Test Lesson', lessons[0].title)

        actions.login(ADMIN_EMAIL)
        xsrf_token = crypto.XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN)
        delete_url = '%s?%s' % (
            self.URL,
            urllib.urlencode({
                'key': skill.id,
                'xsrf_token': cgi.escape(xsrf_token)
            }))
        response = self.delete(delete_url)

        self.assertEquals(200, response.status_int)
        course = courses.Course(None, self.course.app_context)
        skill_map = SkillMap.load(course)
        lessons = skill_map.get_lessons_for_skill(skill)
        self.assertEqual([], lessons)

    def test_delete_prerequisites(self):
        skill_graph = SkillGraph.load()
        src_skill = skill_graph.add(Skill.build(SKILL_NAME, SKILL_DESC))
        tgt_skill = skill_graph.add(Skill.build(SKILL_NAME_2, SKILL_DESC_2))
        skill_graph.add_prerequisite(tgt_skill.id, src_skill.id)
        skill_graph = SkillGraph.load()
        self.assertEqual(1, len(skill_graph.prerequisites(tgt_skill.id)))

        # delete prerequisite
        actions.login(ADMIN_EMAIL)
        xsrf_token = crypto.XsrfTokenManager.create_xsrf_token(self.XSRF_TOKEN)
        response = self._put(
            version='1',
            name=tgt_skill.name,
            description=tgt_skill.description,
            prerequisite_ids=[],
            xsrf_token=xsrf_token,
            key=tgt_skill.id)

        self.assertEqual(200, response['status'])
        self.assertEqual('Saved.', response['message'])

        skill_graph = SkillGraph.load()
        prerequisites = skill_graph.prerequisites(tgt_skill.id)
        self.assertEqual(0, len(prerequisites))


class SkillMapHandlerTests(actions.TestBase):
    ADMIN_EMAIL = 'admin@foo.com'
    COURSE_NAME = 'skill_map_course'
    DASHBOARD_SKILL_MAP_URL = 'dashboard?action=skill_map'
    SKILL_MAP_URL = 'modules/skill_map?action=skill_map&tab=skills_table'
    GRAPH_URL = 'modules/skill_map?action=skill_map&tab=dependency_graph'

    def setUp(self):
        super(SkillMapHandlerTests, self).setUp()

        self.base = '/' + self.COURSE_NAME
        context = actions.simple_add_course(
            self.COURSE_NAME, self.ADMIN_EMAIL, 'Skill Map Course')
        self.old_namespace = namespace_manager.get_namespace()
        namespace_manager.set_namespace('ns_%s' % self.COURSE_NAME)

        self.course = courses.Course(None, context)
        self.unit = self.course.add_unit()
        self.unit.title = 'Unit 1'
        self.lesson = self.course.add_lesson(self.unit)
        self.lesson.title = 'Lesson 1'
        self.course.save()

        actions.login(self.ADMIN_EMAIL, is_admin=True)

    def tearDown(self):
        del sites.Registry.test_overrides[sites.GCB_COURSES_CONFIG.name]
        namespace_manager.set_namespace(self.old_namespace)
        super(SkillMapHandlerTests, self).tearDown()

    def test_redirect_to_skill_map_handler(self):
        response = self.get(self.DASHBOARD_SKILL_MAP_URL)
        self.assertEqual(302, response.status_int)
        response = self.get(response.location)
        self.assertEqual(200, response.status_int)

    def test_rejected_if_not_authorized(self):
        actions.login('student@foo.com')
        response = self.get(self.SKILL_MAP_URL)
        self.assertEqual(302, response.status_int)

    def test_empty_skills_table(self):
        response = self.get(self.SKILL_MAP_URL)
        self.assertEqual(200, response.status_int)
        dom = self.parse_html_string(response.body)
        section_title = dom.find('.//div[@id="gcb-section"]/h3')
        self.assertEqual(
            'Skills Table', (''.join(section_title.itertext())).strip())

    def test_dependency_graph_tab(self):
        response = self.get(self.GRAPH_URL)
        self.assertEqual(200, response.status_int)

        dom = self.parse_html_string(response.body)
        assert dom.find('.//div[@class="graph"]')


class StudentSkillViewWidgetTests(BaseSkillMapTests):

    def setUp(self):
        super(StudentSkillViewWidgetTests, self).setUp()
        actions.login(ADMIN_EMAIL)

        self.unit = self.course.add_unit()
        self.unit.title = 'Test Unit'
        self.lesson = self.course.add_lesson(self.unit)
        self.lesson.title = 'Test Lesson'
        self.course.save()

    def _getWidget(self):
        url = 'unit?unit=%(unit)s&lesson=%(lesson)s' % {
            'unit': self.unit.unit_id, 'lesson': self.lesson.lesson_id}
        dom = self.parse_html_string(self.get(url).body)
        return dom.find('.//div[@class="skill-panel"]')

    def test_skills_widget_supressed_by_course_settings(self):
        # Skill widget is not shown if supressed by course setting
        env = {'course': {'display_skill_widget': False}}
        with actions.OverriddenEnvironment(env):
            self.assertIsNone(self._getWidget())

        # But the skill widget *is* shown if the course setting is True or is
        # unset
        self.assertIsNotNone(self._getWidget())

        env = {'course': {'display_skill_widget': True}}
        with actions.OverriddenEnvironment(env):
            self.assertIsNotNone(self._getWidget())

    def test_no_skills_in_lesson(self):
        # Expect the title is the only content
        widget = self._getWidget()
        all_children = widget.findall('./*')
        self.assertEqual(1, len(all_children))
        child = all_children[0]
        self.assertEqual('div', child.tag)
        actions.assert_contains('lesson-title', child.attrib['class'])
        actions.assert_contains('Test Lesson', child.text)

    def test_skills_with_no_prerequisites_or_successors(self):
        # Expect skills shown and friendly messages for prerequ and successors
        skill_graph = SkillGraph.load()
        sa = skill_graph.add(Skill.build('a', 'describe a'))
        sb = skill_graph.add(Skill.build('b', 'describe b'))
        self.lesson.properties[LESSON_SKILL_LIST_KEY] = [sa.id, sb.id]
        self.course.save()

        widget = self._getWidget()
        title_div, skills_div, details_div, control_div = widget.findall('./*')

        actions.assert_contains('Test Lesson', title_div.text)

        actions.assert_contains('Skills in this lesson', skills_div.text)

        li_list = skills_div.findall('.//li[@class="skill"]')
        self.assertEqual(2, len(li_list))
        actions.assert_contains('a', li_list[0].text)
        actions.assert_contains(
            'describe a', li_list[0].attrib['data-skill-description'])
        actions.assert_contains('b', li_list[1].text)
        actions.assert_contains(
            'describe b', li_list[1].attrib['data-skill-description'])

        details_xml = cElementTree.tostring(details_div)
        actions.assert_contains('doesn\'t depend on', details_xml)
        actions.assert_contains('isn\'t a prerequisite', details_xml)

    def test_skills_with_prerequisites_and_successors(self):
        # Set up lesson with two skills, B and C, where A is a prerequisite of B
        # and D is a successor of B. Expect to see A and D listed in the
        # 'depends on' and 'leads to' sections respectively
        skill_graph = SkillGraph.load()
        sa = skill_graph.add(Skill.build('a', 'describe a'))
        sb = skill_graph.add(Skill.build('b', 'describe b'))
        sc = skill_graph.add(Skill.build('c', 'describe c'))
        sd = skill_graph.add(Skill.build('d', 'describe d'))

        skill_graph.add_prerequisite(sb.id, sa.id)
        skill_graph.add_prerequisite(sd.id, sc.id)

        self.lesson.properties[LESSON_SKILL_LIST_KEY] = [sb.id, sc.id]
        self.course.save()

        widget = self._getWidget()

        # Check B and C are listed as skills in this lesson
        skills_in_lesson = widget.findall('./div[2]//li[@class="skill"]')
        self.assertEqual(2, len(skills_in_lesson))
        actions.assert_contains('b', skills_in_lesson[0].text)
        actions.assert_contains('c', skills_in_lesson[1].text)

        # Skill A is listed in the "depends on" section
        depends_on = widget.findall('./div[3]/div[1]/ol/li')
        self.assertEqual(1, len(depends_on))
        self.assertEqual(str(sa.id), depends_on[0].attrib['data-skill-id'])

        # Skill D is listed in the "leads to" section
        leads_to = widget.findall('./div[3]/div[2]/ol/li')
        self.assertEqual(1, len(leads_to))
        self.assertEqual(str(sd.id), leads_to[0].attrib['data-skill-id'])

        # But if Skill A is also taught in this lesson, don't list it
        self.lesson.properties[LESSON_SKILL_LIST_KEY].append(sa.id)
        self.course.save()
        widget = self._getWidget()
        depends_on = widget.findall('./div[3]/div[1]/ol/li')
        self.assertEqual(0, len(depends_on))

    def test_skills_cards_have_title_description_and_lesson_links(self):
        # The lesson contains Skill A which has Skill B as a follow-on. Skill B
        # is found in Lesson 2. Check that the skill card shown for Skill B in
        # Lesson 1 has correct information
        skill_graph = SkillGraph.load()
        sa = skill_graph.add(Skill.build('a', 'describe a'))
        sb = skill_graph.add(Skill.build('b', 'describe b'))
        skill_graph.add_prerequisite(sb.id, sa.id)

        self.lesson.properties[LESSON_SKILL_LIST_KEY] = [sa.id]
        lesson2 = self.course.add_lesson(self.unit)
        lesson2.title = 'Test Lesson 2'
        lesson2.properties[LESSON_SKILL_LIST_KEY] = [sb.id]
        self.course.save()

        widget = self._getWidget()
        leads_to = widget.findall('./div[3]/div[2]/ol/li')
        self.assertEqual(1, len(leads_to))
        card = leads_to[0]
        name = card.find('.//div[@class="name"]').text
        description = card.find(
            './/div[@class="description"]/div[@class="content"]').text
        locations = card.findall('.//ol[@class="locations"]/li/a')
        self.assertEqual('b', name.strip())
        self.assertEqual('describe b', description.strip())
        self.assertEqual(1, len(locations))
        self.assertEqual('1.2', locations[0].text.strip())
        self.assertEqual(
            'unit?unit=%(unit)s&lesson=%(lesson)s' % {
                'unit': self.unit.unit_id, 'lesson': lesson2.lesson_id},
            locations[0].attrib['href'])


class SkillMapMetricTests(BaseSkillMapTests):
    """Tests for the functions in file skill_map_metrics"""

    def test_nxgraph(self):
        """The graph of SkillMapMetrics and the skill_map are equivalent."""
        self._build_sample_graph()
        skill_map = SkillMap.load(self.course)
        nxgraph = SkillMapMetrics(skill_map).nxgraph
        self.assertIsInstance(nxgraph, DiGraph)
        successors = skill_map.build_successors()
        # Check nodes
        self.assertEqual(len(nxgraph), len(successors))
        for skill in successors:
            self.assertIn(skill, nxgraph.nodes(),
                          msg='Node {} not found in nx graph.'.format(skill))
        # Check edges
        original_edges = sum(len(dst) for dst in successors.values())
        self.assertEqual(len(nxgraph.edges()), original_edges)
        for src, dst in nxgraph.edges_iter():
            self.assertIn(src, successors)
            self.assertIn(dst, successors[src],
                          msg='Extra {},{} edge in nx graph.'.format(src, dst))

    def test_find_cycles_no_cycle(self):
        """The input is a directed graph with no cycles. Expected []."""
        self._build_sample_graph()
        skill_map = SkillMap.load(self.course)
        self.assertEqual(SkillMapMetrics(skill_map).simple_cycles(), [])

    def test_find_cycles_one_cycle(self):
        """The input is a directed graph with only 1 cycle."""
        self._build_sample_graph()
        # Adding cycle a -> d -> a
        self.skill_graph.add_prerequisite(self.sa.id, self.sd.id)
        skill_map = SkillMap.load(self.course)
        self.assertEqual(6, len(skill_map.skills()))
        successors = skill_map.build_successors()
        self.assertEqual(
            sorted(SkillMapMetrics(skill_map).simple_cycles()[0]),
            [self.sa.id, self.sd.id])

    def test_find_cycles_multiple_cycles(self):
        """The input is a directed graph with two cycles."""
        self._build_sample_graph()
        # Adding cycle a -> d -> a
        self.skill_graph.add_prerequisite(self.sa.id, self.sd.id)
        # Adding cycle g -> h -> g
        sg = self.skill_graph.add(Skill.build('g', ''))
        sh = self.skill_graph.add(Skill.build('h', ''))
        self.skill_graph.add_prerequisite(sg.id, sh.id)
        self.skill_graph.add_prerequisite(sh.id, sg.id)

        expected = [[self.sa.id, self.sd.id], [sg.id, sh.id]]
        skill_map = SkillMap.load(self.course)
        successors = skill_map.build_successors()
        result = SkillMapMetrics(skill_map).simple_cycles()
        self.assertEqual(len(result), len(expected))
        for cycle in result:
            self.assertIn(sorted(cycle), expected)

    def test_find_cycles_not_conected(self):
        """The input is a directed graph whith an isolated scc."""
        self._build_sample_graph()
        # Adding cycle g -> h -> g
        sg = self.skill_graph.add(Skill.build('g', ''))
        sh = self.skill_graph.add(Skill.build('h', ''))
        self.skill_graph.add_prerequisite(sg.id, sh.id)
        self.skill_graph.add_prerequisite(sh.id, sg.id)
        skill_map = SkillMap.load(self.course)
        expected0 = [sg.id, sh.id]
        successors = skill_map.build_successors()
        result = SkillMapMetrics(skill_map).simple_cycles()
        self.assertEqual(sorted(result[0]), expected0)

    def test_find_cycles_empty(self):
        """The input is an empty graph."""
        skill_map = SkillMap.load(self.course)
        self.assertEqual(
            SkillMapMetrics(skill_map).simple_cycles(), [])