from classes.fingerprints import Fingerprints
from classes.requester import Requester
from classes.matcher import Match
from collections import Counter
import requests, re, hashlib
from html.parser import HTMLParser


class DiscoverRedirect(object):

	def __init__(self, url):
		self.org = url
		self.url = url

		# if a schema is not provided default to http
		if not url.startswith("http"): self.url = "http://" + url

		try:
			r = requests.get(self.url, verify=False)
		
			if not r.url == self.url:
				# ensure that folders and files are removed
				parts = r.url.split('//')
				http, netloc = parts[0:2]

				# remove subfolders and/or files
				# http://example.com/test -> http://example.com/
				if '/' in netloc:
					self.url = http + '//' + netloc.split('/')[0] + '/'
				else:
					self.url = http + '//' + netloc + '/'
		except:
			self.url = None

	# check if the host redirects to another location
	def is_redirected(self):
		return not self.org == self.url

	# return a cleaned URL
	def get_valid_url(self):
		return self.url

		

class DiscoverErrorPage(object):
	# find error pages on the site
	# the requester has a built-in list of items and patterns
	# to remove before calculating a checksum of pages that
	# should not exists

	def __init__(self, requester, host, url_list):
		self.host = host
		self.urls = url_list
		self.error_pages = set()
		self.requester = requester


	def run(self):
		urls = [ [{'host': self.host, 'url': u}] for u in self.urls ]

		self.requester.set_fingerprints(urls)
		self.requester.set_find_404(True)

		results = self.requester.run()
		while results.qsize() > 0:
			response = results.get()
			self.error_pages.add(response)


	def get_error_pages(self):
		return self.error_pages



class DiscoverCMS(object):

	def __init__(self, requester, matcher, ordered_fingerprints, chunk_size):
		self.fps = ordered_fingerprints
		self.fps_iter = iter(self.fps)
		self.chunk_size = chunk_size
		self.index = 0
		self.matcher = matcher
		self.requester = requester


	def is_done(self):
		return self.index >= len(self.fps)


	def run(self, host, cms_skip_list):

		i = self.index
		cs = self.chunk_size

		# extract a chunck
		chunk = self.fps[i:i+cs]
		self.index += cs

		self.requester.set_fingerprints(chunk)
		results = self.requester.run()

		# process the results and find matches
		while results.qsize() > 0:
			fps,response = results.get()

			matches = self.matcher.get_result(fps, response)
			if matches:
				return [cms['cms'] for cms in matches]

		return []





class DiscoverVersion(object):
	def __init__(self, requester, matcher, result, chunk_size):
		self.result = result
		self.chunk_size = chunk_size
		self.matcher = matcher
		self.requester = requester


	def run(self, host, fingerprints):
		cs = self.chunk_size

		num_fp = len(fingerprints)
		for i in range(0, num_fp, cs):
			chunk = fingerprints[i:i+cs]

			self.requester.set_fingerprints(chunk)
			results = self.requester.run()

			while results.qsize() > 0:
				fps,response = results.get()
				matches = self.matcher.get_result(fps, response)
				for fp in matches:
					self.result.add_cms(fp)



class DiscoverOS(object):
	def __init__(self, cache, results, fingerprints):
		self.cache = cache
		self.results = results

		self.category = "Operating System"
		self.os = Counter()
		self.packages = Counter()
		self.oss = []
		self.matched_packages = set()
		self.fingerprints = fingerprints


	def find_match(self, response):
		headers = response.headers
		if 'server' in headers:
			line = headers['server']
			if "(" in line:
				os = line[line.find('(')+1:line.find(')')]
				line = line[:line.find('(')-1] + line[line.find(')')+1: ]
			else:
				os = False

			if os: self.oss.append(os.lower())

			for part in line.split(" "):
				try:
					pkg,version = list(map(str.lower, part.split('/')))
					self.packages[pkg] += 1

					os_list = self.fingerprints[pkg][version]

					for i in os_list:
						if len(i) == 2:
							os, os_version = i
							weight = 1
						elif len(i) == 3:
							os, os_version, weight = i

						self.matched_packages.add( (os, os_version, pkg, version) )
						self.os[(os, os_version)] += weight

				except Exception as e:
					continue

		if 'X-Powered-By' in headers:
			line = headers['X-Powered-By']
			try:
				pkg,version =  list(map(str.lower, line.split('/')))
				for i in self.fingerprints[pkg][version]:
					if len(i) == 2:
						os, os_version = i
						weight = 1
					elif len(i) == 3:
						os, os_version, weight = i
					
					self.matched_packages.add( (os, os_version, pkg, version) )
					self.os[(os, os_version)] += weight
			except Exception as e:
				pass

	def finalize(self):
		# if an os string 'self.oss' has been found in the header
		# prioritize the identified os's in self.os

		# iterate over the list of os strings found
		for os in self.oss:
			# iterate over the fingerprinted os's
			for key in self.os:
				if os in key[0].lower():
					self.os[key] += 100

		# add OS to results: self.os: {(os, version): weight, ...}
		results = []
		for p in self.os:
			results.append({'version': p[1], 'os': p[0], 'count': self.os[p]})

		if len(results) == 0: return

		prio = sorted(results, key=lambda x:x['count'], reverse=True)
		max_count = prio[0]['count']
		relevant = []
		for i in prio:
			if i['count'] == max_count:
				if len(relevant) > 0  and i[0] == "": continue
				self.results.add(self.category, i['os'], i['version'], i['count'])
			else:
				break


	def run(self):
		headers = set()
		responses = self.cache.get_responses()
		for response in responses:
			self.find_match(response)

		self.finalize()



# Used by the DiscoverMore crawler
# The
class LinkExtractor(HTMLParser):
	def __init__(self, strict):
		super().__init__(strict=strict)
		self.results = set()

	def get_results(self):
		return self.results

	def handle_starttag(self, tag, attrs):
		try:
			url = ''
			if tag == 'script' or tag == 'img':
				for attr in attrs: 
					if attr[0] == 'src':  self.results.add(attr[1])
			if tag == 'link':
				for attr in attrs: 
					if attr[0] == 'href': self.results.add(attr[1])
		except:
			pass


class DiscoverMore(object):

	def __init__(self, host, requester, cache, fingerprints, matcher, results):
		self.host = host
		self.requester = requester
		self.cache = cache
		self.fingerprints = fingerprints
		self.matcher = matcher
		self.result = results
		self.threads = 10

	
	def run(self):
		resources = set()
		parser = LinkExtractor(strict=False)

		for req in self.cache.get_responses():
			# skip pages that do not set 'content-type'
			# these might be binaries
			if not 'content-type' in req.headers:
				continue

			# only scrape pages that can contain links/references
			if 'text/html' in req.headers['content-type']:
				parser.feed(str(req.content))
				
				for i in parser.get_results():
					
					# ensure that only resources located on the domain /sub-domain is requested 
					if i.startswith('http'):
						parts = i.split('/')
						host = parts[2]

						# if the resource is out side of the domain, skip it
						if not host in self.host.split('/')[2]:
							continue

						# else update the url so that it only contains the relative location
						else:
							i = '/'.join(parts[3:])

					resources.add( i )

		# the items in the resource set should mimic a list of fingerprints:
		# a fingerprint is a dict with at least an URL key
		urls = [ [{'url':i}] for i in resources ]
		
		# fetch the discovered resources.
		# As this class' purpose only is to fetch the resource (add to cache)
		# there is no return value, or further actions needed
		for i in range(0, len(urls), self.threads):
			self.requester.set_fingerprints( urls[i:i+self.threads] )
			results = self.requester.run()

		# find matches for all the responses in the cache
		for response in self.cache.get_responses():
			matches = self.matcher.get_result(self.fingerprints, response)
			for fp in matches:
				self.result.add_cms(fp)
	














