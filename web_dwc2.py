# A Json api to get data from klippy through http
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import json
import threading
# for webserver
import tornado.web
import base64
import uuid
import os
import datetime
import re
import time
import util
import shutil

class web_dwc2:

##
#	Stuff arround klippers extras logic
##
	#	init who?
	def __init__(self, config):

		self.klipper_ready = False
		#	get config
		self.config = config
		self.adress = config.get( 'listen_adress', "127.0.0.1" )
		self.port = config.getint( "listen_port", 4711 )
		self.webpath = config.get( 'web_path', "dwc2/web" )
		self.printername =  config.get( 'printer_name', "Klipper" )
		#	klippy objects
		self.printer = config.get_printer()
		self.reactor = self.printer.get_reactor()
		self.gcode = self.printer.lookup_object('gcode')
		self.configfile = self.printer.lookup_object('configfile')
		#	gcode execution needs
		self.gcode_queue = []	#	containing gcode user pushes from dwc2
		self.gcode_reply = []	#	contains the klippy replys
		self.klipper_macros = []
		self.gcode.dwc_lock = False
		self.gcode.register_respond_callback(self.gcode_response) #	if thers a gcode reply, phone me -> see fheilmans its missing in master
		#	once klipper is ready start pre_flight function - not happy with this. If klipper fails to launch -> no web if?
		self.printer.register_event_handler("klippy:ready", self.handle_ready)
		self.printer.register_event_handler("klippy:disconnect", self.shutdown)
		self.start_time = time.time()
		#	grab stuff from config file
		self.klipper_config = self.printer.get_start_args()['config_file']
		con_ = self.configfile.read_main_config()
		self.sdpath = con_.getsection("virtual_sdcard").get("path", None)
		if not self.sdpath:
			logging.error( "DWC2 failed to start, no sdcard configured" )
			return
		self.kin_name = con_.getsection("printer").get("kinematics")
		self.web_root = self.sdpath + "/" + self.webpath
		if not os.path.isfile( self.web_root + "/" + "index.html" ):
			logging.error( "DWC2 failed to start, no webif found in " + self.web_root )
			return
		# manage client sessions
		self.sessions = {}
		self.status_0 = {}
		self.status_1 = {}
		self.status_2 = {}
		self.status_3 = {}
		self.dwc2()
		logging.basicConfig(level=logging.DEBUG)
	#	function once reactor calls, once klipper feels good
	def handle_ready(self):
		#	klippy related
		self.toolhead = self.printer.lookup_object('toolhead', None)
		self.sdcard = self.printer.lookup_object('virtual_sdcard', None)
		self.fan = self.printer.lookup_object('fan', None)
		self.chamber = self.printer.lookup_object('chamber', None)
		#	hopeflly noone get more than 4 extruders up :D
		self.extruders = [
			self.printer.lookup_object('extruder0', None) ,
			self.printer.lookup_object('extruder1', None) ,
			self.printer.lookup_object('extruder2', None) ,
			self.printer.lookup_object('extruder3', None)
		]
		self.heater_bed = self.printer.lookup_object('heater_bed', None)
		self.kinematics = self.toolhead.get_kinematics()

		# print data for tracking layers during print
		self.print_data = {}
		self.printfile = None
		self.cached_file_info = None
		self.klipper_ready = True
		self.get_klipper_macros()
	#	reactor calls this on klippy restart
	def shutdown(self):
		#	kill the thread here
		logging.info( "DWC2 shuting down - as klippy is shutdown" )
		self.http_server.stop()
		self.sessions = {}

##
#	Webserver and related
##
	#	launch webserver
	def dwc2(self):
		def tornado_logger(req):
			fressehaltn = []
			fressehaltn = [ "/favicon.ico", "/rr_status?type=1", "/rr_status?type=2", "/rr_status?type=3", "/rr_reply" ]
			values = [req.request.remote_ip, req.request.method, req.request.uri]
			if req.request.uri not in fressehaltn:
				logging.info("DWC2 tornado: ".join(values))	#	bind this to debug later
		def launch_tornado(application):
			#time.sleep(10)	#	delay startup so dwc2 can timeout
			logging.info( "DWC2 starting at: http://" + str(self.adress) + ":" + str(self.port) )
			self.http_server = tornado.httpserver.HTTPServer( application )
			self.http_server.listen( self.port )
			tornado.ioloop.IOLoop.instance().start()
		def debug_console(self):
			logging.debug( "Start debbug console:\n")
			import pdb; pdb.set_trace()
		#
		cookie_secret = base64.b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes) # random cookie
		# define the threading aplication
		application = tornado.web.Application(
			[
				tornado.web.url(r"/css/(.*)", tornado.web.StaticFileHandler, {"path": self.web_root + "/css/"}),
				tornado.web.url(r"/js/(.*)", tornado.web.StaticFileHandler, {"path": self.web_root + "/js/"}),
				tornado.web.url(r"/fonts/(.*)", tornado.web.StaticFileHandler, {"path": self.web_root + "/fonts/"}),
				tornado.web.url(r"/(rr_.*)", self.req_handler, { "web_dwc2": self } ),
				tornado.web.url(r"/.*", self.dwc_handler,{"p_": self.web_root}, name="main"),
			],
			cookie_secret=cookie_secret,
			log_function=tornado_logger)
		self.tornado = threading.Thread( target=launch_tornado, args=(application,) )
		self.tornado.daemon = True
		self.tornado.start()

		dbg = threading.Thread( target=debug_console, args=(self,) )
		#dbg.start()
	# the main webpage to serve the client browser itself
	class dwc_handler(tornado.web.RequestHandler):

		def initialize(self, p_):
			self.web_root = p_

		def get(self):
			def index():
				rq_path = self.web_root + "/index.html"
				self.render( rq_path )

			if self.request.uri == "/":	#	redirect ti index.html(dwc2)
				index()
			elif ".htm" in self.request.uri: # covers others like dwc1
				self.render( self.web_root + self.request.uri )
			elif ".json" in self.request.uri: # covers dwc1 settingsload
				if os.path.isfile(self.web_root + self.request.uri):
					pass
				else:
					self.write( { "err": 1 } )
			elif os.path.isfile(self.web_root + self.request.uri):
				with open(self.web_root + self.request.uri, "rb") as f:
					self.write( f.read() )
					self.finish()

			else:
				logging.warn( "DWC2 - unhandled request in dwc_handler: " + self.request.uri + " redirecting to index.")
				index()
	#for handling request dwc2 is sending
	class req_handler(tornado.web.RequestHandler):

		def initialize(self, web_dwc2):

			self.web_dwc2 = web_dwc2
			self.repl_ = {"err":1}

		def get(self, *args):

			if self.request.remote_ip not in self.web_dwc2.sessions.keys() and "rr_connect" not in self.request.uri:
				#	response 408 timeout to make the webif reload after klippy restarts us
				self.clear()
				self.set_status(408)
				self.finish()
				return

			# dwc connect request
			if "rr_connect" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_connect(self)
			#	there is no rr_configfile so return empty
			elif "rr_configfile" in self.request.uri:
				self.repl_ = { "err" : 0 }
			elif "rr_config" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_config()
			#	filehandling - delete files/folders
			elif "rr_delete" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_delete( self )
			#	disconnect
			elif "/rr_disconnect" in self.request.uri:
				self.repl_ = { "err" : 0 }
			#	filehandling . download
			elif "rr_download" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_download(self)
			#	filehandling - dirlisting
			elif "rr_filelist" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_filelist(self)
			#	filehandling - dirlisting for dwc 1
			elif "rr_files" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_files(self)
			#	filehandling - fileinfo / gcodeinfo
			elif "rr_fileinfo" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_fileinfo(self)
			#	gcode request
			elif "rr_gcode" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_gcode( self )
				return
			#	filehandling - dircreation
			elif "rr_mkdir" in self.request.uri:
				self.web_dwc2.rr_mkdir(self)
				return
			elif "rr_move" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_move(self)
			#	gcode reply
			elif "rr_reply" in self.request.uri:
				while self.web_dwc2.gcode_reply:
					msg = self.web_dwc2.gcode_reply.pop(0).replace("!!", "Error: ")
					self.write( msg )
				return

			#	status replys
			elif "rr_status" in self.request.uri:

				if not self.web_dwc2.klipper_ready:
					self.web_dwc2.rr_status_0()
					self.repl_ = self.web_dwc2.status_0

				else:
					# there are 3 types of status requests:
					t_ = int( self.get_argument('type') )

					if t_ == 1:
						self.web_dwc2.rr_status_1()
						self.repl_ = self.web_dwc2.status_1

					elif t_ == 2:
						self.web_dwc2.rr_status_2()
						self.repl_ = self.web_dwc2.status_2

					elif t_ == 3:
						self.web_dwc2.rr_status_3()
						self.repl_ = self.web_dwc2.status_3

					else:
						logging.warn(" DWC2 - error in rr_status \n" + str(t_) )


			if self.repl_ == {"err":1}:
				logging.warn("DWC2 - unhandled ?GET? " + self.request.uri)

			try:
				self.write( json.dumps(self.repl_) )
			except Exception as e:
				logging.warn( "DWC2 - error in write: " + str(e) )

		def post(self, *args):

			#	filehandling - uploads
			if "rr_upload" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_upload(self)

			try:
				self.write( json.dumps(self.repl_) )
			except Exception as e:
				logging.warn( "DWC2 - error in write: " + str(e) )

##
#	All the crazy shit dc42 fiddled together in c
##
	#	dwc rr_connect
	def rr_connect(self, web_):

		#	better error?
		repl_ = {
			"err":0,
			"sessionTimeout":8000,	#	config value?
			"boardType":"unknown"	#	has klippy this data?
		}

		user_cookie = web_.request.headers.get("Cookie")
		user_ip = web_.request.remote_ip

		self.sessions[user_ip] = user_cookie

		return repl_
	#	dwc rr_config
	def rr_config(self):

		try:
			max_acc = self.toolhead.max_accel
			max_vel = self.toolhead.max_velocity
			rails_ = self.kinematics.rails
			extru_ = self.extruders
		except Exception as e:
			max_acc = []
			max_vel = []
			rails_ = []
			extru_ = []
		ax_ = [[],[]]
		for r_ in rails_:

			min_pos, max_pos = r_.get_range()
			ax_[0].append(min_pos)
			ax_[1].append(max_pos)

		repl_ = {
			"axisMins": [ x for x in ax_[0] ],
			"axisMaxes": [ x for x in ax_[1] ],
			"accelerations": [ max_acc for x in ax_[0] ],
			"currents": [ 0 for x in ax_[0] ] + [ 0 for ex_ in extru_ if ex_ is not None ] ,	#	can we fetch data from tmc drivers here ?
			"firmwareElectronics": util.get_cpu_info(),
			"firmwareName": "Klipper",
			"firmwareVersion": self.printer.get_start_args()['software_version'],
			"dwsVersion": self.printer.get_start_args()['software_version'],
			"firmwareDate": "2018-12-24b1",	#	didnt get that from klippy
			"idleCurrentFactor": 35,
			"idleTimeout": 5,
			"minFeedrates": [ 5 for x in ax_[0] ] + [ 5 for ex_ in extru_ if ex_ is not None ] ,
			"maxFeedrates": [ max_vel for x in ax_[0] ] + [ min(75,max_vel) for ex_ in extru_ if ex_ is not None ]	#	unitconversion ?
		}

		return repl_
	#	simple dir/fileremover
	def rr_delete(self, web_):

		#	lazymode_
		path_ = self.sdpath + web_.get_argument('name').replace("0:", "")

		if os.path.isdir(path_):
			shutil.rmtree(path_)

		if os.path.isfile(path_):
			os.remove(path_)

		return {'err': 0}
	#	dwc rr_download - lacks logging
	def rr_download(self, web_):

		path_ = self.sdpath + web_.get_argument('name').replace("0:", "")

		#	ovverride for config file
		if "/sys/" in path_ and "config.g" in web_.get_argument('name').replace("0:", ""):
			path_ = self.klipper_config

		if os.path.isfile(path_):

			#	handles regular files
			web_.set_header( 'Content-Type', 'application/force-download' )
			web_.set_header( 'Content-Disposition', 'attachment; filename=%s' % os.path.basename(path_) )

			with open(path_, "rb") as f:
				web_.write( f.read() )

			return {'err':0}
		else:
			return {"err":1}
	#	dwc rr_files - dwc1 thing
	def rr_files(self, web_):
		#		{
		#			"dir":"0:/gcodes",
		#			"first":0,
		#			"files":[
		#				"testcube.gcode",
		#				"Hevo",
		#				"xy_joint_backbrace_left.gcode",
		#				"VORON",
		#				"Calibration",
		#				"Hevo_Fusion",
		#				"blabla",
		#				"3D_Scanner",
		#				"Alex",
		#				"Hevo_Reinforced",
		#				"snurk_ring_2cm_Type1-_V2.gcode",
		#				"*Hevo_RS",
		#				"Fucktopus_easy.gcode"],
		#			"next":0,
		#			"err":0
		#		}
		path_ = self.sdpath + web_.get_argument('dir').replace("0:", "")

		repl_ = {
			"dir": web_.get_argument('dir') ,
			"first": web_.get_argument('first', 0) ,
			"files": [] ,
			"next": 0 ,
			"err": 0
		}

		#	if rrf is requesting directory, it has to be there.
		if not os.path.exists(path_):
			os.makedirs(path_)

		#	append elements to files list matching rrf syntax
		for el_ in os.listdir(path_):
			el_path = path_ + "/" + el_
			repl_['files'].append( "*" + str(el_) if os.path.isdir(el_path) else str(el_) )

		return repl_
	#	dwc rr_filelist
	def rr_filelist(self, web_):

		path_ = self.sdpath + web_.get_argument('dir').replace("0:", "")

		#	creating the infoblock
		repl_ = {
			"dir": web_.get_argument('dir') ,
			"first": web_.get_argument('first', 0) ,
			"files": [] ,
			"next": 0
		}

		#	if rrf is requesting directory, it has to be there.
		if not os.path.exists(path_):
			os.makedirs(path_)

		#	append elements to files list matching rrf syntax
		for el_ in os.listdir(path_):
			el_path = path_ + "/" + el_
			repl_['files'].append({
				"type": "d" if os.path.isdir(el_path) else "f" ,
				"name": str(el_) ,
				"size": os.stat(el_path).st_size ,
				"date": datetime.datetime.utcfromtimestamp( os.stat(el_path).st_mtime ).strftime("%Y-%m-%dT%H:%M:%S")
			})

		#	add klipper macros as virtual files
		if "/macros" in web_.get_argument('dir').replace("0:", ""):
			for macro_ in self.klipper_macros:

				repl_['files'].append({
					"type": "f" ,
					"name": macro_ ,
					"size": 1 ,
					"date": time.strftime("%Y-%m-%dT%H:%M:%S") 
				})

		#	virtual config file
		elif "/sys" in web_.get_argument('dir').replace("0:", ""):

			repl_['files'].append({
				"type": "f",
				"name": "config.g" ,
				"size": os.stat(self.klipper_config).st_size ,
				"date": datetime.datetime.utcfromtimestamp( os.stat(self.klipper_config).st_mtime ).strftime("%Y-%m-%dT%H:%M:%S")
			})

		return repl_
	#	dwc fileinfo - getting gcode info
	def rr_fileinfo(self, web_):

		#import pdb; pdb.set_trace()

		#	hits if we are in printing state
		if web_.get_argument('name', default=None) is not None:
			path_ = self.sdpath + web_.get_argument('name').replace("0:", "")
		else:
			path_ = self.sdcard.current_file.name
			if self.cached_file_info is not None:
				self.cached_file_info['printDuration'] = self.toolhead.print_time
				return self.cached_file_info

		if not os.path.isfile(path_):
			repl_ = { "err": 1 }

		repl_ = self.read_gcode(path_)
		return repl_
	#	dwc rr_gcode - append to gcode_queue
	def rr_gcode(self, web_):

		#	handover to klippy as: [ "G28", "M114", "G1 X150", etc... ]
		gcodes = str( web_.get_argument('gcode') ).replace('0:', '').replace('"', '').split("\n")

		rrf_commands = {
			'G10': self.cmd_G10 ,		#	set heaters temp
			'M0': self.cmd_M0 ,			#	cancel SD print
			'M32': self.cmd_M32 ,		#	Start sdprint
			'M98': self.cmd_M98 ,		#	run macro
			'M106': self.cmd_M106 ,		#	set fan
			'M290': self.cmd_M290 ,		#	set babysteps
			'M999': self.cmd_M999		#	issue restart
		}

		#	tell the webif that we are pending with given number of commands - will it wait for same number of replys ????("seq")
		web_.write( json.dumps({'buff': len(gcodes), 'err': 0}) )
		web_.finish()

		#	start to prepare commands
		while gcodes:

			#	parse commands - still magic. Wheres the original function in klipper?
			params = self.parse_params(gcodes.pop(0))

			#	defaulting to original
			handover = params['#original']

			#	rewrite rrfs specials to klipper readable format
			if params['#command'] in rrf_commands.keys():
				func_ = rrf_commands.get(params['#command'])
				handover = func_(params)
				if handover == 0:
					continue

			self.gcode_queue.append(handover)

		self.reactor.register_callback(self.gcode_reactor_callback)
	#	dwc rr_move - backup printer.cfg
	def rr_move(self, web_):

		if "/sys/" in web_.get_argument('old').replace("0:", "") and "config.g" in web_.get_argument('old').replace("0:", ""):
			src_ = self.klipper_config
			dst_ = self.klipper_config + ".backup"

		else:
			src_ = self.sdpath + web_.get_argument('old').replace("0:", "")
			dst_ = self.sdpath + web_.get_argument('new').replace("0:", "")

		try:
			shutil.copyfile( src_ , dst_)
		except Exception as e:
			return {"err": 1}

		return {"err": 0}
	#	dwc rr_mkdir
	def rr_mkdir(self, web_):

		path_ = self.sdpath + web_.get_argument('dir').replace("0:", "")

		if not os.path.exists(path_):
			os.makedirs(path_)
			return {'err': 0}

		return {'err': 1}
	# 	rr_status_0 if klipper is down/failed to start
	def rr_status_0(self):

		#	just put in things really needed to make dwc2 happy

		self.status_0.update({
			"status": self.get_printer_status(),
			"seq": len(self.gcode_reply),
			"coords": {
				"xyz": [] ,
				"machine": [] ,
				"extr": []
			},
			"speeds": {},
			"sensors": {
				"fanRPM": 0
			},
			"params": {
				"fanPercent": [] ,
				"extrFactors": []
			} ,
			"temps": {
				"bed": { "active": 0 },
				"extra": [{}],
				"current": [],
				"tools": {
					"active": []
				},
				"names": []
			} ,
			"probe": {} ,
			"axisNames": "" ,
			"tools": [] ,
			"volumes": 1,
			"mountedVolumes": 1 ,
			"name": self.printername
		})
	#	dwc rr_status 1
	def rr_status_1(self):
		now = self.reactor.monotonic()

		extr_stat = self.get_extr_stats(now)
		bed_stats = self.get_bed_stats(now)
		gcode_stats = self.gcode.get_status(now)
		if self.fan:
			fan_stats = [ self.fan.get_status(now) ]	#	this can be better
		else:
			fan_stats = []

		self.status_1.update({
			"status": self.get_printer_status(),
			"coords": {
				"axesHomed": self.get_axes_homed(),
				"xyz": self.toolhead.get_position()[:3] ,
				"machine": [ 0, 0, 0 ],			#	what ever this is? no documentation.
				"extr": self.toolhead.get_position()[3:]
			},
			"speeds": {
				"requested": gcode_stats['speed'] / 60 ,
				"top": 	0 #	not available on klipepr
			},
			"currentTool": 1,	#	must be at least 1 ! learned the hardway....
			"params": {
				"atxPower": 0,
				"fanPercent": [ fan_['speed']*100 for fan_ in fan_stats ] ,
				"speedFactor": gcode_stats['speed_factor'] * 100,
				"extrFactors": [ gcode_stats['extrude_factor'] * 100 ],
				"babystep": gcode_stats['homing_zpos']
			},
			"seq": len(self.gcode_reply),
			"sensors": {
				"probeValue": 0,
				"fanRPM": 0
			},
			"temps": {
				"bed": {
					"current": bed_stats['actual'] ,
					"active": bed_stats['target'] ,
					"state": bed_stats['state'] ,
					"heater": 0
				},
				#"chamber": {
				#	"current": 19.5,
				#	"active": 0,
				#	"state": 0,
				#	"heater": 2
				#},
				"current": [ bed_stats['actual'] ] + [ ex_['actual'] for ex_ in extr_stat ] ,
				"state": [ bed_stats['state'] ] + [ ex_['state'] for ex_ in extr_stat ] ,
				"tools": {
					"active": [ [ ex_['target'] ] for ex_ in extr_stat ],
					"standby": [ [ 0 ] for ex_ in extr_stat ]
				},
				"extra": [
					{
						"name": "*MCU",
						"temp": 0
					}
				]
			},
			"time": time.time() - self.start_time
		})

		if self.chamber:
			chamber_stats = self.chamber.get_status(0)
			self.status_1['temps'].update({
				"chamber": {
					"current": chamber_stats.get("temp") ,
					"active": chamber_stats.get("target", -1) ,
					"state": chamber_stats.get("state") ,
					"heater": 2 ,	#	extruders + bett ?
					},
					"current": self.status_1['temps']['current'] + [ chamber_stats.get("temp") ],
					"state": self.status_1['temps']['state'] + [ chamber_stats.get("state") ] ,
				})
	#	dwc rr_status 2
	def rr_status_2(self):
		now = self.reactor.monotonic()

		extr_stat = self.get_extr_stats(now)
		bed_stats = self.get_bed_stats(now)
		gcode_stats = self.gcode.get_status(now)
		if self.fan:
			fan_stats = [ self.fan.get_status(now) ]	#	this can be better
		else:
			fan_stats = []

		#	dummy data
		self.status_2.update({
			"status": self.get_printer_status() ,
			"coords": {
				"axesHomed": self.get_axes_homed() ,
				"xyz": self.toolhead.get_position()[:3] ,
				"machine": [ 0, 0, 0 ] ,
				"extr": self.toolhead.get_position()[3:]
			},
			"speeds": {
				"requested": gcode_stats['speed'] / 60 ,
				"top": 	0 #	not available on klipepr
			},
			"currentTool": 1 ,
			"params": {
				"atxPower": 0 ,
				"fanPercent": [ fan_['speed']*100 for fan_ in fan_stats ] ,
				"fanNames": [ "" for fan_ in fan_stats ],
				"speedFactor": gcode_stats['speed_factor'] * 100,
				"extrFactors": [ gcode_stats['extrude_factor'] * 100 ],
				"babystep": gcode_stats['homing_zpos']
			},
			"seq": len(self.gcode_reply),
			"sensors": {
				"probeValue": 0,
				"fanRPM": 0
			},
			"temps": {
				"bed": {
					"current": bed_stats['actual'] ,
					"active": bed_stats['target'] ,
					"state": bed_stats['state'] ,
					"heater": 0
				},
				#"chamber": {
				#	"current": 19.6,
				#	"active": 0,
				#	"state": 0,
				#	"heater": 2
				#},
				"current": [ bed_stats['actual'] ] + [ ex_['actual'] for ex_ in extr_stat ] ,
				"state": [ bed_stats['state'] ] + [ ex_['state'] for ex_ in extr_stat ] ,
				"names": [ "Bed" ] + [ ex_['name'] for ex_ in extr_stat ] ,
				"tools": {
					"active": [ [ ex_['target'] ] for ex_ in extr_stat ],
					"standby": [ [ 0 ] for ex_ in extr_stat ]
				},
				"extra": [
					{
						"name": "*MCU",
						"temp": 0
					}
				]
			},
			"time": time.time() - self.start_time,
			"coldExtrudeTemp": max( [ ex_['min_extrude_temp'] for ex_ in extr_stat ] ),
			"coldRetractTemp": max( [ ex_['min_extrude_temp'] for ex_ in extr_stat ] ),
			"compensation": "None",
			"controllableFans": len( fan_stats ),
			"tempLimit": max( ex_['max_temp'] for ex_ in extr_stat ),
			"endstops": 4088,	#	what does this do?
			"firmwareName": "Klipper",
			"geometry": self.kin_name,
			"axes": len(self.get_axes_homed()),
			"totalAxes": len(self.get_axes_homed()) + len( [ 1 for ex_ in extr_stat ] ),
			"axisNames": "XYZ", #+ "".join([ "U" for ex_ in extr_stat ]),
			"volumes": 1,
			"mountedVolumes": 1,
			"name": self.printername,
			"probe": {
				"threshold": 100,
				"height": 0,
				"type": 8
			},
			"tools": [
				{
					"number": extr_stat.index(ex_) + 1 ,
					"name": ex_['name'] ,
					"heaters": [ extr_stat.index(ex_) + 1 ],
					"drives": [ extr_stat.index(ex_) + 1 ] ,
					"axisMap": [ 1 ],
					"fans": 1,
					"filament": "",
					"offsets": [ 0, 0, 0 ]
				} for ex_ in extr_stat ]
			,
			#"mcutemp": {
			#	"min": 30.1,
			#	"cur": 36.8,
			#	"max": 37
			#},
			#"vin": {
			#	"min": 24.2,
			#	"cur": 24.3,
			#	"max": 24.5
			#}
		})
		if self.chamber:
			chamber_stats = self.chamber.get_status(0)
			self.status_2['temps'].update({ 
				"chamber": {
					"current": chamber_stats.get("temp") ,
					"active": chamber_stats.get("target", -1) ,
					"state": chamber_stats.get("state") ,
					"heater": 2 ,	#	extruders + bett ?
					},
					"current": self.status_2['temps']['current'] + [ chamber_stats.get("temp") ],
					"state": self.status_2['temps']['state'] + [ chamber_stats.get("state") ] ,
					"names": self.status_2['temps']['names'] + [ "Chamber" ]
				})
	#	dwc rr_status 3
	def rr_status_3(self):
		#	nested here as its related to fileinfo_3 only
		def manage_print_data():

			#	init print data on started print
			if not self.print_data:

				self.print_data = {
					"print_start": time.time() ,
					"print_dur": 0 ,
					"extr_start": sum(self.toolhead.get_position()[3:]) ,
					"firstlayer_dur": 0 ,
					"curr_layer": 1 ,
					"curr_layer_start": 0 ,
					"curr_layer_dur" : 0 ,
					"heat_time": 0 ,
					"zhop": False ,
					"last_zposes": [ self.toolhead.get_position()[3] for n_ in range(6) ]
				}

			self.z_mean = round( sum(self.print_data['last_zposes']) / len(self.print_data['last_zposes']) , 2 )

			if self.print_data['curr_layer_start'] == 0 \
					and self.print_data['extr_start'] + 80 < sum(self.toolhead.get_position()[3:]): 	#	firstlayerdetec min 80mm extrude (skirt?purge?)
				#	now we know firstlayer started + heating ended
				self.print_data.update({
					'curr_layer_start': time.time() ,
					'heat_time': time.time() - self.print_data.get('print_start', 0) ,
					"last_zposes":	[ self.toolhead.get_position()[3] for n_ in range(6) ]
					})

			if self.z_mean < gcode_stats['last_zpos']:
				# curr zpos raised
				self.print_data['zhop'] = True
			elif self.z_mean > gcode_stats['last_zpos']:
				# curr zpos is now lower as history mean so it was a travel zhop
				self.print_data['zhop'] = False
			if self.z_mean == gcode_stats['last_zpos'] \
					and self.print_data['zhop']:
				self.print_data['zhop'] = False
				#
				if self.print_data['firstlayer_dur'] == 0:
					self.print_data['firstlayer_dur'] = self.print_data['curr_layer_dur']
				self.print_data['curr_layer_start'] = time.time()
				self.print_data['curr_layer'] += 1
				self.print_data['curr_layer_dur'] = 0

			if self.print_data['curr_layer_start'] != 0:
				self.print_data['curr_layer_dur'] = time.time() - self.print_data['curr_layer_start']

			#	first out, actual in - a rolling list
			self.print_data['last_zposes'].pop(0)
			self.print_data['last_zposes'].append(gcode_stats['last_zpos'])
			self.print_data['print_dur'] = time.time() - self.print_data['print_start']

		now = self.reactor.monotonic()

		self.extr_stat = self.get_extr_stats(now)
		extr_stat = self.extr_stat
		bed_stats = self.get_bed_stats(now)
		gcode_stats = self.gcode.get_status(now)
		if self.fan:
			fan_stats = [ self.fan.get_status(now) ]	#	this can be better
		else:
			fan_stats = []

		manage_print_data()

		self.status_3.update({
			"status": self.get_printer_status() ,
			"coords": {
				"axesHomed": self.get_axes_homed() ,
				"xyz": self.toolhead.get_position()[:3] ,
				"machine": [ 0, 0, 0 ] ,
				"extr": self.toolhead.get_position()[3:]
			},
			"speeds": {
				"requested": gcode_stats['speed'] / 60 ,
				"top": 	0 #	not available on klipepr
			},
			"currentTool": 1 ,
			"params": {
				"atxPower": 0 ,
				"fanPercent": [ fan_['speed']*100 for fan_ in fan_stats ] ,
				"fanNames": [ "" for fan_ in fan_stats ],
				"speedFactor": gcode_stats['speed_factor'] * 100,
				"extrFactors": [ gcode_stats['extrude_factor'] * 100 ],
				"babystep": gcode_stats['homing_zpos']
			},
			"seq": len(self.gcode_reply),
			"sensors": {
				"probeValue": 0,
				"fanRPM": 0
			},
			"temps": {
				"bed": {
					"current": bed_stats['actual'] ,
					"active": bed_stats['target'] ,
					"state": bed_stats['state'] ,
					"heater": 0
				},
				"current": [ bed_stats['actual'] ] + [ ex_['actual'] for ex_ in extr_stat ] ,
				"state": [ bed_stats['state'] ] + [ ex_['state'] for ex_ in extr_stat ] ,
				"names": [ "Bed" ] + [ ex_['name'] for ex_ in extr_stat ],
				"tools": {
					"active": [ [ ex_['target'] ] for ex_ in extr_stat ],
					"standby": [ [ 0 ] for ex_ in extr_stat ]
				},
				"extra": [
					{
						"name": "*MCU",
						"temp": 0
					}
				]
			},
			"time": time.time() - self.start_time,
			"currentLayer": self.print_data.get('curr_layer', 1) ,
			"currentLayerTime": self.print_data.get('curr_layer_dur', 1),
			"extrRaw": [ sum([ ex_['pos'] for ex_ in extr_stat ]) - self.print_data.get('extr_start', 1) ],
			"fractionPrinted": self.sdcard.get_status(now).get('progress', 0) , # percent done
			"filePosition": self.sdcard.file_position,
			"firstLayerDuration": self.print_data.get('firstlayer_dur', 1) if self.print_data.get('firstlayer_dur', 0) > 0 else self.print_data.get('curr_layer_dur', 1),
			"firstLayerHeight": self.cached_file_info.get('firstLayerHeight', 0.2),
			"printDuration": self.print_data.get('print_dur', 1) ,
			"warmUpDuration": self.print_data.get('heat_time', 1),
			"timesLeft": {
				"file": (1-self.sdcard.get_status(now).get('progress', 0)) * self.cached_file_info.get('printTime', 1),
				"filament": (1-( sum(self.toolhead.get_position()[3:]) - self.print_data.get("extr_start", 1) ) \
								/ sum(self.cached_file_info.get( "filament", 1) ) ) * self.cached_file_info.get('printTime', 1),
				"layer": (1-self.print_data.get('curr_layer', 1) / self.cached_file_info.get('layercount', 1) ) * self.cached_file_info.get('printTime', 1)
			}
		})

		if self.chamber:
			chamber_stats = self.chamber.get_status(0)
			self.status_3['temps'].update({
				"chamber": {
					"current": chamber_stats.get("temp") ,
					"active": chamber_stats.get("target", -1) ,
					"state": chamber_stats.get("state") ,
					"heater": 2 ,	#	extruders + bett ?
					},
					"current": self.status_3['temps']['current'] + [ chamber_stats.get("temp") ],
					"state": self.status_3['temps']['state'] + [ chamber_stats.get("state") ] ,
					"names": self.status_3['temps']['names'] + [ "Chamber" ]
				})
	#	dwc rr_upload - uploading files to sdcard
	def rr_upload(self, web_):

		path_ = self.sdpath + web_.get_argument('name').replace("0:", "")
		dir_ = os.path.dirname(path_)

		ret_ = {"err":1}

		if not os.path.exists(dir_):
			os.makedirs(dir_)

		#	klipper config ecxeption
		if "/sys/" in path_ and "config.g" in web_.get_argument('name').replace("0:", ""):
			path_ = self.klipper_config

		with open(path_, 'w') as out:
			out.write(web_.request.body)

		if os.path.isfile(path_):
			ret_ = {"err":0}

		return ret_

##
#	Gcode execution related stuff
##

	#	rrf G10 command - set heaterstemp
	def cmd_G10(self, params):

		tool = params['P']
		temp = max(self.gcode.get_float('S', params, 0.), self.gcode.get_float('R', params, 0.))	#	not fully get this - John
		command_ = str("M104 T%d S%0.2f" % (int(tool)-1,float(temp)))
		return command_
	#	rrf M0 - cancel print from sd
	def cmd_M0(self, params):

		self.sdcard.must_pause_work = True 		#	pause print -> sdcard postion is saved in virtual sdcard
		self.sdcard.file_position = 0			#	reset fileposition
		self.sdcard.work_timer = None 			#	reset worktimer
		self.sdcard.current_file = None 		#	
		self.printfile = None

		#	let user define a cancel/pause print macro`?
		return 0
	#	rrf M32 - start print from sdcard
	def cmd_M32(self, params):

		#	file dwc1 - 'zzz/simplify3D41.gcode'
		#	file dwc2 - '/gcodes/zzz/simplify3D41.gcode'

		file = '/'.join(params['#original'].split(' ')[1:])
		if '/gcodes/' not in file:	#	DWC 1 work arround
			fullpath = self.sdpath + '/gcodes/' + params['#original'].split()[1]
		else:
			fullpath = self.sdpath + file

		#	load a file to scurrent_file if its none
		if not self.sdcard.current_file:
			if os.path.isfile(fullpath):
				self.printfile = open(fullpath, 'rb')				#	get file object as klippy would do
				self.printfile.seek(0, os.SEEK_END)
				self.printfile.seek(0)
				self.sdcard.current_file = self.printfile 			#	set it as current file
				self.sdcard.file_position = 0 						#	postions / size
				self.sdcard.file_size = os.stat(fullpath).st_size
				self.cached_file_info = self.read_gcode(fullpath) 	#	refresh cached file info - its used during printng
				self.print_data = None 								#	reset printdata as this is a new print
			else:
				import pdb; pdb.set_trace()
				raise 'gcodefile' + fullpath + ' not found'

		return 'M24'
	#	rrf run macro
	def cmd_M98(self, params):

		path = self.sdpath + "/" + "/".join(params['#original'].split("/")[1:])

		if not os.path.exists(path):
			#	now we know its no macro file
			klipma = params['#original'].split("/")[-1].replace("\"", "")
			if klipma in self.klipper_macros:
				return klipma
			else:
				return 0
		else:
			#	now we know its a macro from dwc
			with open( path ) as f:
				lines = f.readlines()

			for line in [x.strip() for x in lines]:
				self.gcode_queue.append(line)

			return 0
	#	rrf M106 translation to klipper scale
	def cmd_M106(self, params):

		if float(params['S']) < .05:
			command = str("M107")
		else:
			command = str( params['#command'] + " S" + str(int( float(params['S']) * 255 )) )

		return command
	#	fo ecxecuting m112 now!
	def cmd_M112(self, params):
		self.printer.invoke_shutdown('Emergency Stop from DWC 2')
	#	setting babysteps:
	def cmd_M290(self, params):

		if self.get_axes_homed()[2] == 0:
			self.gcode_reply.append('!! Only idiots try to babystep withoung homing !!')
			return 0

		mm_step = self.gcode.get_float('Z', params, None)
		if not mm_step: mm_step = self.gcode.get_float('S', params, None)	#	DWC 1 workarround
		params = self.parse_params('SET_GCODE_OFFSET Z_ADJUST%0.2f' % mm_step)
		self.gcode.cmd_SET_GCODE_OFFSET(params)
		self.gcode_reply.append('Z adjusted by %0.2f' % mm_step)

		return 0
	#	rrf restart command
	def cmd_M999(self, params):
		#needs0 otherwise the printer gets restarted after emergency buttn is pressed
		return 0
	#	getting response by callback
	def gcode_response(self, msg):
		
		if self.klipper_ready:
			stat_ = self.get_printer_status()
			if stat_ in [ 'S', 'P', 'D' ]:
				#SPD? seriously? printing state
				if re.match('T\d:\d+.\d\s/\d+.\d+', msg): return	#	filters temmessages during heatup

		self.gcode_reply.append(msg)

		#import pdb; pdb.set_trace()
	#	recall for gcode ecxecution is needed ( threadsafeness )
	def gcode_reactor_callback(self, eventtime):
		#	if user adds commands return the callback
		if self.gcode.dwc_lock:
			return

		ack_needers = [ "G0", "G1", "G28", "M0", "M24", "M25", "M83", "M84", "M104", "M112", "M140", "M141" ]

		self.gcode.dwc_lock = self.gcode.is_processing_data = True

		while self.gcode_queue:

			params = self.parse_params(self.gcode_queue.pop(0))
			logging.error( "entering: " + params['#command'] )
			try:
				handler = self.gcode.gcode_handlers.get(params['#command'], self.gcode.cmd_default)
				handler(params)
			except Exception as e:
				self.gcode_reply.append( "" )
				logging.error( "failed: " + params['#command'] + str(e) )
				time.sleep(1)	#	not beautiful but webif ignores errors on button commands otherwise
				self.gcode_reply.append( "!! " + str(e) + "\n" )
			else:
				logging.error( "passed: " + params['#command'] )
				if params['#command'] in ack_needers or params['#command'] in self.klipper_macros:
					self.gcode_reply.append( "" )	#	pseudo ack

		self.gcode.dwc_lock = self.gcode.is_processing_data = False
	#	parses gcode commands into params -took from johns work
	def parse_params(self, line):
		args_r = re.compile('([A-Z_]+|[A-Z*/])')
		line = origline = line.strip()
		cpos = line.find(';')
		if cpos >= 0:
			line = line[:cpos]
		# Break command into parts
		parts = args_r.split(line.upper())[1:]
		params = { parts[i]: parts[i+1].strip()
					for i in range(0, len(parts), 2) }
		params['#original'] = origline
		if parts and parts[0] == 'N':
			# Skip line number at start of command
			del parts[:2]
		if not parts:
			# Treat empty line as empty command
			parts = ['', '']
		params['#command'] = cmd = parts[0] + parts[1].strip()

		return params

	#	return status for infoblock parts taken from Fheilmann

##
#	Helper functions getting/parsing data
##

	def get_printer_status(self):

		#	case 'F': return 'updating';
		#	case 'O': return 'off';
		#	case 'H': return 'halted';
		#	case 'D': return 'pausing';
		#	case 'S': return 'paused';
		#	case 'R': return 'resuming';
		#	case 'P': return 'processing';	?printing?
		#	case 'M': return 'simulating';
		#	case 'B': return 'busy';
		#	case 'T': return 'changingTool';
		#	case 'I': return 'idle';

		if "Printer is ready" != self.printer.get_state_message():
			self.klipper_ready = False
			return "O"

		if self.sdcard.current_file:
			if self.sdcard.must_pause_work:
				return "D" if self.sdcard.work_timer else "S"
			if self.sdcard.current_file and self.sdcard.work_timer:
				return "P"

		if self.gcode.is_processing_data:
			return "B"

		return "I"
	#	import klipper macros as virtual files
	def get_klipper_macros(self):

		for key_ in self.gcode.gcode_help.keys():

			if self.gcode.gcode_help[key_] == "G-Code macro":

				self.klipper_macros.append( key_.upper() )
	#	same what gcode.py is doing
	def get_axes_homed(self):
		#	self.toolhead.get_next_move_time() - self.reactor.monotonic()
		homed = []
		for rail in self.kinematics.rails:

			if rail.is_motor_enabled():
				homed.append(1)
			else:
				homed.append(0)
		if not homed:
			homed = [0,0,0]
		return homed
	#	stats for extruders
	def get_extr_stats(self, now):

		# position - self.extruders[0].extrude_pos
		# temps - self.extruders[0].heater.get_status(self.reactor.monotonic()) - {'temperature': 21.225501996279693, 'target': 0.0}
		# temps - self.extruders[0].heater.stats(self.reactor.monotonic())
		extr_stats = []

		for ex_ in self.extruders:

			if ex_ is not None:

				status = ex_.heater.get_status(now)

				app_ = {
					'name': re.sub('\d', '', ex_.name) + str( self.extruders.index(ex_) ) ,
					'pos': ex_.extrude_pos ,
					'actual': status['temperature'] ,
					'target': status['target'] ,
					'state': 0 if status['target'] < 20 else 2 ,
					'min_extrude_temp': ex_.heater.min_extrude_temp ,
					'max_temp': ex_.heater.max_temp
				}

				extr_stats.append( app_ )
		return extr_stats
	#	stats for a heatedbed
	def get_bed_stats(self, now):

		if self.heater_bed is not None:

			#	// 0: off, 1: standby, 2: active, 3: fault (same for bed)
			#	{'temperature': 25.99274317011324, 'target': 0.0}
			status = self.heater_bed.get_status(now)

			ret_ = {
				"actual": status['temperature'] ,
				"target": status['target'] ,
				"state": 0 if status['target'] < 20 else 2
			}
		else:
			ret_ = {
				"actual": 0 ,
				"target": 0 ,
				"state": 0
			}
		return ret_
	#	read and parse gcode files
	def read_gcode(self, path_):

		#	looks complicated but should be good maintainable
		#	the heigth of all objects - regex
		objects_h = [
			'\sZ\\d+.\\d*' ,					# 	kisslicer
			'' ,								# 	Slic3r
			'\sZ\\d+.\\d*' ,					# 	S3d
			'G1\sZ\d*\.\d*' ,					# 	Slic3r PE
			'\sZ\\d+.\\d*' ,					# 	Cura
			'\sZ\d+.\d{3}'					#	ideamaker
			]

			#	heigth of the first layer
		first_h = [ 
			'first_layer_thickness_mm\s=\s\d+\.\d+' , 		#	kisslicers setting
			'; first_layer_height =' ,						# 	Slic3r
			'\sZ\\d+.\\d*' ,								#	Simplify3d
			'G1\sZ\d*\.\d*' ,								#	Slic3r PE
			'\sZ\\d+.\\d\s' ,								#	Cura
			';LAYER:0\n;Z:\d+.\d{3}'									#	ideamaker
			]

		#	the heigth of layers
		layer_h = [
			'layer_thickness_mm\s=\s\d+\.\d+' ,				#	kisslicer
			'' ,											#	Slic3r
			';\s+layerHeight.*' ,							#	S3d
			'; layer_height = \d.\d+' ,						#	Slic3r PE
			';Layer height: \d.\d+' ,						# 	Cura
			';Z:\d+.\d{3}'									#	ideamaker
			]
		#	slicers estimate print time
		time_e = [
			'\s\s\d*\.\d*\sminutes' , 						#	Kisslicer
			'; estimated printing time' ,					#	Slic3r
			';\s+Build time:.*' ,							#	S3d
			'\d+h?\s?\d+m\s\d+s' ,							#	Slic3r PE
			';TIME:\\d+' ,									#	Cura
			';Print Time:\s\d+\.?\d+'						#	ideamaker
			]
		#	slicers filament usage
		filament = [
			'Ext 1 =.*mm' ,									#	Kisslicer
			';.*filament used =' ,							#	Slic3r
			';.*Filament length: \d+.*\(' ,					#	S3d
			'.*filament\sused\s=\s.*mm' ,					#	Slic3r PE ; filament used =
			';Filament used: \d*.\d+m'	,					#	Cura
			';Material#1 Used:\s\d+\.?\d+'					#	ideamaker
			]
		#	slicernames
		slicers = [ 
			'KISSlicer' ,
			'^Slic3r$' ,
			'Simplify3D\(R\).*' ,
			'Slic3r Prusa Edition\s.*\so',
			'Cura_SteamEngine.*' ,
			'ideaMaker\s([0-9]*\..*,)'
			]
		#
		meta = { "slicer": "Slicer is not implemented" }

		def calc_time(in_):
			if in_ == -1: return in_
			#import pdb; pdb.set_trace()
			h_str = re.search(re.compile('(\d+(\s)?hours|\d+(\s)?h)'),in_)
			m_str = re.search(re.compile('(([0-9]*\.[0-9]+)\sminutes|\d+(\s)?m)'),in_)
			s_str = re.search(re.compile('(\d+(\s)?seconds|\d+(\s)?s)'),in_)
			dursecs = 0
			if h_str:
				dursecs += float( max( re.findall('([0-9]*\.?[0-9]+)' , ''.join(h_str.group()) ) ) ) *3600 
			if m_str:
				dursecs += float( max( re.findall('([0-9]*\.?[0-9]+)' , ''.join(m_str.group()) ) ) ) *60 
			if s_str:
				dursecs += float( max( re.findall('([0-9]*\.?[0-9]+)' , ''.join(s_str.group()) ) ) )
			if dursecs == 0:
				dursecs = float( max( re.findall('([0-9]*\.?[0-9]+)' , in_) ) )

			return dursecs

		#	get 4k lines from file
		with open(path_, 'rb') as f:
			cont_ = f.readlines()			#	gimme the whole file
		int_ = cont_[:2000] + cont_[-2000:] 	# 	build up header and footer
		pile = " ".join(int_)					#	build a big pile for regex
		#	determine slicer
		sl = -1
		for regex in slicers:
			#	resource gunner ?
			if re.compile(regex).search(pile):
				#import pdb; pdb.set_trace()
				meta['slicer'] = re.search(re.compile(regex),pile).group()
				sl = slicers.index(regex)
				break
		#	only grab metadata if we found a slicer
		if sl > -1 :
			#import pdb; pdb.set_trace()
			if objects_h[sl] != "":
				try:
					matches = re.findall(objects_h[sl], pile )
					meta['objects_h'] = float( max( re.findall("\d*\.\d*", ' '.join(matches) ) ) ) # max strings works?	
				except:
					pass				
			if layer_h[sl] != "":
				try:
					matches = re.findall(layer_h[sl], pile )
					meta['layer_h'] = float( min( re.findall("\d*\.\d*", ' '.join(matches) ) ) ) # min strings works?
				except:
					pass
			if first_h[sl] != "":
				try:
					matches = re.findall(first_h[sl], pile )
					meta['first_h'] = float( min( re.findall("\d*\.\d*", ' '.join(matches) ) ) ) # min strings works?
				except:
					pass
			if time_e[sl] != "":
				try:
					matches = re.findall(time_e[sl], pile )
					meta['time_e'] = max( matches )
					meta['time_e'] = calc_time(meta['time_e'])	#	bring time to seconds
				except:
					pass
			if filament[sl] != "":
				try:
					matches = re.findall(filament[sl], pile )
					meta['filament'] = float( max( re.findall("\d*\.\d*", ' '.join(matches) ) ) ) # max strings works?
					meta['filament'] = (meta['filament'],meta['filament']*1000)[sl==4]	#	cura is in m -> translate
				except:
					pass
			
		else:
			self.gcode_reply.append("Your Slicer is not yet implemented.")

		#	put it in rrf format
		repl_ = {
			"size": int(os.stat(path_).st_size) ,
			"lastModified": str(datetime.datetime.utcfromtimestamp( os.stat(path_).st_mtime ).strftime("%Y-%m-%dT%H:%M:%S")) ,
			"height": float( meta.get("objects_h",1 ) ) ,
			"firstLayerHeight": meta.get("first_h",1 ) ,
			"layerHeight": float( meta.get("layer_h",1) ) ,
			"printTime": int( meta.get("time_e",1) ) ,			# in seconds
			"filament": [ float( meta.get("filament",1) ) ] ,		# in mm
			"generatedBy": str( meta.get("slicer","<<Slicer not implemented>>") ) ,
			"fileName": '0:' + str(path_).replace(self.sdpath, '') ,
			"layercount": ( float(meta.get("objects_h",1)) - meta.get("first_h",1) ) / float(meta.get("layer_h",1) ) + 1 ,
			"err": 0
		}

		return repl_


	#	helpful if you mussed something in status 0-3
	def dict_compare(self, d1, d2):

		#	self.dict_compare( self.status_1 , self.status_2 )
		for key_ in d1.keys():

			if d1.get(key_) != d2.get(key_) :

				print( key_ + " is different in d2.:\nd1:")
				print( json.dumps(d1.get(key_)) )
				print("vs d2 :")
				print(json.dumps(d2.get(key_)))
				print("\n")

def load_config(config):
	return web_dwc2(config)
