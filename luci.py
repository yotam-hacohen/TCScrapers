#!/usr/bin/env python3

import json
import os
import re
import requests 
import sys

# A set of utility functions for downloading results from the CI system

# For a given gerritt patch and patchset, and given CI builder
# (e.g. "linux-blink"), get the build numbers of any completed try jobs.
def getBuildNumbers(patchNumber, patchset, builder):
  headers = {"accept": "application/json", "accept-encoding": "gzip, deflate, br"}
  payload = {"requests":[{"searchBuilds":{"pageSize":1000,"predicate":{"includeExperimental":False,"gerritChanges":[{"host":"chromium-review.googlesource.com","project":"chromium/src","change":patchNumber,"patchset":patchset}]},"mask":{"fields":"id,builder,number,tags,status,critical,createTime,startTime,endTime,summaryMarkdown,infra.resultdb,ancestorIds","outputProperties":[{"path":["rts_was_used"]}]}}}]}
  response = requests.post('https://cr-buildbucket.appspot.com/prpc/buildbucket.v2.Builds/Batch', json=payload, headers=headers)
  buildInfo = json.loads(response.text[5:])
  return [b['number'] for b in buildInfo["responses"][0]["searchBuilds"]["builds"] if b['builder']['builder'] == builder]

# For a given CI builder and build number, get information about the build,
# including individual step information.
def getBuildInfo(builderName, buildNum):
  headers = {'accept': 'application/json', 'accept-encoding': 'gzip, deflate, br'}
  payload = {'requests': [{'getBuild': {'mask': {'allFields': True}, 'builder': {'project': 'chromium', 'bucket': 'try', 'builder': builderName}, 'buildNumber': buildNum}}]}
  r = requests.post('https://cr-buildbucket.appspot.com/prpc/buildbucket.v2.Builds/Batch', json=payload, headers=headers)
  if r.status_code != 200:
    raise RuntimeError('Error downloading build info for build number' % buildNum)
  j = json.loads(r.text[5:])
  return j['responses'][0]['getBuild']

# Given build info as returned by getBuildInfo() above, and the name of a build
# step (e.g., "blink_web_tests"), return the total bot shard time it took to run
# the step.
def getShardTime(buildInfo, stepName):
  stepInfo = [step for step in buildInfo['steps'] if step['name'].startswith('%s (with patch)' % stepName)]
  if len(stepInfo) == 0:
    raise RuntimeError('Could not find step %s' % stepName)
  m = re.match(r".*Total shard runtime \((\d+)m (\d+)s\)", stepInfo[0]['summaryMarkdown'])
  if not m:
    raise RuntimeError('Could not find total shard runtime for %s' % stepName)
  return int(m[1]) + (int(m[2])/60)

# Given build info as returned by getBuildInfo() above, and the name of a build
# step, return the URL of the stdout log for the step.
def getLogUrl(buildInfo, stepName):
  stepInfo = [step for step in buildInfo['steps'] if step['name'].startswith('%s (with patch)' % stepName)]
  if len(stepInfo) == 0:
    raise RuntimeError('Could not find step %s' % stepName)
  logs = [log for log in stepInfo[0]['logs'] if log['name'] == 'stdout']
  if len(logs) == 0:
    raise RuntimeError('Could not find stdout log for step %s for build %d' % (stepName, buildNum))
  if (not logs[0]['viewUrl']):
    raise RuntimeError('Could not find URL for stdout log for step %s' % stepName)
  return logs[0]['viewUrl'] + '?format=raw'

# For a given CI builder and build number, and the name of a step that generates
# a web_test results page (e.g. "blink_web_tests" or "blink_wpt_tests"), return
# the test result information used to populate the custom web_test results page.
def getWebTestResults(builderName, buildNum, stepName):
  url = 'https://chromium-layout-test-archives.storage.googleapis.com/chromium/try/{}/{}/{} (with patch)/full_results_jsonp.js'.format(builderName, buildNum, stepName)
  r = requests.get(url)
  if r.status_code != 200:
    raise RuntimeError('Could not fetch %s' % url)
  false = False
  true = True
  null = 'null'
  j = {}
  def SET_TASK_IDS(x):
    j.update({'task_ids': x})
  def ADD_FULL_RESULTS(x):
    j.update(x)
  t = r.text
  p = t.rfind('SET_TASK_IDS([', -1000)
  try:
    eval(t[:p-2])
  except:
    print("First eval failed")
  try:
    eval(t[p:-1])
  except:
    print("second eval failed")
  return j

# Given web_test results data as returned by getWebTestResults() above, and the
# name of a single test case (e.g., "fast/dom/allowed-children.html"), return
# the URL's of all output artifacts captured from the test (e.g., actual PNG,
# diff PNG, text/diff, ...).
def getArtifacts(webTestResults, testName):
  if not testName.startswith('/'):
    testName = '/' + testName
  request_headers = {
    'Accept': 'application/json',
    'Content-Type': 'application/json'
  }
  task_ids = [i[:-1] + '1' if i.endswith('0') else i for i in webTestResults['task_ids']]
  invocations = ["invocations/task-chromium-swarm.appspot.com-%s" % i for i in task_ids]
  predicate = {
    'testResultPredicate': {
      'expectancy': 'ALL',
      'testIdRegexp': 'ninja://:[^/]*' + re.sub(r'([.?+])', r'\\\1', testName)
    }
  }
  payload = {
    'invocations': invocations,
    'predicate': predicate,
    'pageSize': 1000
  }
  response = requests.post(
    'https://results.api.cr.dev/prpc/luci.resultdb.v1.ResultDB/QueryArtifacts',
    headers=request_headers,
    json=payload)
  if response.status_code != 200:
    raise RuntimeError('Could not load artifacts')
  t = response.text
  if t.startswith(")]}'"):
    t = t[4:]
  return json.loads(t)
