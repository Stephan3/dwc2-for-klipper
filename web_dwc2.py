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

	def __init__(self, config):

		self.klipper_ready = False
		#	get config
		self.config = config
		self.adress = config.get( 'listen_adress', "127.0.0.1" )
		self.port = config.getint( "listen_port", 4711 )
		self.webpath = config.get( 'web_path', "dwc2/web" )
		#	klippy objects
		self.printer = config.get_printer()
		self.reactor = self.printer.get_reactor()
		self.gcode = self.printer.lookup_object('gcode')
		self.configfile = self.printer.lookup_object('configfile')
		#	gcode execution needs
		self.gcode_queue = []	#	containing gcode user pushes from dwc2
		self.gcode_reply = []	#	contains the klippy replys
		self.klipper_macros = []
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
		#	parse klipper macros
		self.dwc2()
		logging.basicConfig(level=logging.DEBUG)

	def handle_ready(self):
		#	klippy related
		self.toolhead = self.printer.lookup_object('toolhead', None)
		self.sdcard = self.printer.lookup_object('virtual_sdcard', None)
		self.fan = self.printer.lookup_object('fan', None)
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
		self.print_data = None
		self.cached_file_info = None
		self.klipper_ready = True
		self.get_klipper_macros()
	#	reactor calls this on klippy restart
	def shutdown(self):
		#	kill the thread here
		logging.info( "DWC2 shuting down - as klippy is shutdown" )
		self.http_server.stop()
		self.sessions = {}
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
				logging.info(" DWC2 - serving path: " + rq_path + "\n")
				self.render( rq_path )
			if self.request.uri == "/":
				index()
			elif self.request.uri == "/favicon.ico":
				rq_path = self.web_root + "/favicon.ico"
				with open(rq_path, "rb") as f:
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
				self.web_dwc2.rr_delete( self )
				return

			#	filehandling - dirlisting
			elif "rr_filelist" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_filelist(self)

			#	filehandling - fileinfo / gcodeinfo
			elif "rr_fileinfo" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_fileinfo(self)

			#	gcode request
			elif "rr_gcode" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_gcode( self.get_argument('gcode') )

			#	filehandling - dircreation
			elif "rr_mkdir" in self.request.uri:
				self.web_dwc2.rr_mkdir(self)
				return

			elif "rr_move" in self.request.uri:
				self.repl_ = self.web_dwc2.rr_move(self)

			#	gcode reply
			elif "rr_reply" in self.request.uri:
				if len( self.web_dwc2.gcode_reply ) > 0 :
					for repl_ in self.web_dwc2.gcode_reply:
						self.write( repl_ + "\n" )
						logging.debug( "handover gcode_reply to webif: " + repl_ )
					self.web_dwc2.gcode_reply = []
				return

			#	status replys
			elif "rr_status" in self.request.uri:

				if not self.web_dwc2.klipper_ready:
					self.repl_ = self.web_dwc2.rr_status_0()

				else:
					# there are 3 types of status requests:
					t_ = int( self.get_argument('type') )

					if t_ == 1:
						self.repl_ = self.web_dwc2.rr_status_1()

					elif t_ == 2:
						self.repl_ = self.web_dwc2.rr_status_2()

					elif t_ == 3:
						self.repl_ = self.web_dwc2.rr_status_3()

					else:
						logging.warn(" DWC2 - error in rr_status \n" + str(t_) )

			#	getn files
			elif "rr_download" in self.request.uri:
				self.web_dwc2.rr_download(self)
				return

			if self.repl_ == {"err":1}:
				logging.warn("DWC2 - unhandled ?GET? " + self.request.uri)

			try:
				self.write( json.dumps(self.repl_) )
			except Exception as e:
				logging.warn( "DWC2 - error in write: " + str(e) )
				import pdb; pdb.set_trace()

		def post(self, *args):

			if "rr_upload" in self.request.uri:
				self.web_dwc2.rr_upload(self)
				return

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

	def rr_delete(self, web_):

		#	lazymode_
		path_ = self.sdpath + web_.get_argument('name').replace("0:", "")

		if "/gcodes" in path_:
			path_ = path_.replace("/gcodes", "")

		if os.path.exists(path_):
			try:
				os.remove(path_)
			except: 
				os.removedirs(path_)

	#	dwc rr_download - lacks logging
	def rr_download(self, web_):

		path_ = self.sdpath + web_.get_argument('name').replace("0:", "")

		#	ovverride for config file
		if "/sys/" in path_ and "config.g" in web_.get_argument('name').replace("0:", ""):
			path_ = self.klipper_config

		#	klipper hates subpath for sdcardprints.
		if "/gcodes" in path_:
			path_ = path_.replace("/gcodes", "")

		if os.path.isfile(path_):

			#	handles regular files
			web_.set_header( 'Content-Type', 'application/force-download' )
			web_.set_header( 'Content-Disposition', 'attachment; filename=%s' % os.path.basename(path_) )

			with open(path_, "rb") as f:
				web_.write( f.read() )
				web_.finish()

		else:

			#	else errors
			web_.write( json.dumps( {"err":1} ) )

	#	dwc rr_filelist
	def rr_filelist(self, web_):

		path_ = self.sdpath + web_.get_argument('dir').replace("0:", "")

		#	creating the infoblock
		repl_ = { 
			"dir": web_.get_argument('dir'),
			"first": web_.get_argument('first'),
			"files": [],
			"next":0
		}
		if not "/gcodes" in path_:

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

		else:#	klipper does not like subpath on sdcard

			path_ = path_.replace("/gcodes", "")

			for el_ in os.listdir(path_):

				el_path = path_ + "/" + el_

				if os.path.isfile(el_path):

					repl_['files'].append({
						"type": "f" ,
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

		def read_gcode(path_):

			#	looks complicated but should be good maintainable
			#	the heigth of all objects - regex
			objects_h = [
				"^G1 X.*Z.*" ,						# 	kisslicer
				"" ,								# 	Slic3r
				"^G1\sZ\d+(.)\d+\sF" ,				# 	S3d
				"^G1 Z" ,							# 	Slic3r PE
				"^G\d\s.*Z\d+.\d$"					# 	Cura
				]

			#	heigth of the first layer
			first_h = [ 
				"first_layer_thickness_mm" , 		#	kisslicers setting
				"; first_layer_height =" ,			# 	Slic3r
				"; layer 1, Z" ,					#	Simplify3d
				"; first_layer_height =" ,			#	Slic3r PE
				";Layer height:" 					#	Cura
				]

			#	the heigth of layers
			layer_h = [
				"layer_thickness_mm" ,				#	kisslicer
				"" ,								#	Slic3r
				";   layerHeight" ,					#	S3d
				"; layer_height =" , 				#	Slic3r PE
				";Layer height:" 					# 	Cura
				]

			#	slicers estimate print time
			time_e = [
				".*Estimated Build Time:" , 		#	Kisslicer
				"; estimated printing time" ,		#	Slic3r
				";   Build time:" ,					#	S3d
				"; estimated printing time" ,		#	Slic3r PE
				"^;TIME:\d+"							#	Cura
				]

			#	slicers filament usage
			filament = [
				";.*Ext 1 =" ,						#	Kisslicer
				";.*filament used =" ,				#	Slic3r
				";.*Filament length: \d+.*\(" ,		#	S3d
				";.*filament used = \d+.*\(" ,		#	Slic3r PE
				";Filament used:"					#	Cura
				]

			slicers = [ 
				"KISSlicer" ,
				"Slic3r Prusa Edition" ,
				"Simplify3D" ,
				"Slic3r",
				"Cura_SteamEngine"
				]

			#

			meta = {
				"objects_h": -1 ,
				"first_h": -1 ,
				"layer_h": -1 ,
				"time_e": -1 ,
				"filament": [ -1 ] ,
				"slicer": "Slicer is not implemented"
			}

			def calc_time(in_):

				if in_ == -1: return in_

				split_ = re.compile('([0-9\.]+)').split(in_) #	seperate list in numbers and words

				h_index = [ i for i, it_ in enumerate(split_) if re.search('(h|hours)', it_) ]   #[0] - 1	#	listposition hours by regx
				m_index = [ i for i, it_ in enumerate(split_) if re.search('(m|minutes)', it_) ] #[0] - 1   #

				dursecs = 0

				if len(h_index) > 0:
					dursecs += float( split_[h_index[0]-1] ) * 3600

				if len(m_index) > 0:
					dursecs += float( split_[m_index[0]-1] ) * 60

				if dursecs == 0:
					dursecs += float(in_)

				return dursecs

			def calc_first_layer(in_):

				#{'printTime': 4620.0, 'firstLayerHeight': '100%', 'err': 0, 'generatedBy': 'Slic3r  Prusa Edition 1.42.0-alpha3+ on 2019-02-01 at 21:06:48',
				# 'lastModified': '2019-02-02T10:58:54', 'height': 16.4, 'layerHeight': 0.2, 'filament': [[-1]], 'size': 6324334}
				in_['first_h'] = float( in_['first_h'].replace("%", "") ) /100 * in_['layer_h']

				return in_

			#	get 4k lines from file
			with open(path_, 'rb') as f:
				cont_ = f.readlines()			#	gimme the whole file
			int_ = cont_[:2000] + cont_[-2000:] 	# 	build up header and footer
			pile = " ".join(int_)					#	build a big pile for regex

			#	determine slicer
			hit_sl = -1
			for s_ in slicers:
				#	resource gunner ?
				if re.compile(s_).search(pile):
					meta['slicer'] = s_
					hit_sl = slicers.index(s_)
					break

			#import pdb; pdb.set_trace()

			#	only grab metadata if we found a slicer
			if hit_sl > -1 :

				#	itter over the lines
				for lin_ in int_:
					handle_ = lin_.strip()

					#	objects_heigth
					if objects_h[hit_sl] != "" and re.compile(objects_h[hit_sl]).match(handle_):
						spl_ = re.compile("((Z|z)?[-+]?[0-9]*\.?[0-9])").split(handle_)
						for i_ in spl_:
							if i_ is not None:
								if len(i_) > 1 and re.compile("(z|Z)").match(i_):
									meta['objects_h'] = float( i_.replace("z","").replace("Z","") )

					# 	heigth per layers
					if layer_h[hit_sl] != "" and layer_h[hit_sl] in handle_:
						meta['layer_h'] = float( re.compile("(=( )?|:( )?|,( )?)").split(handle_)[-1:][0].replace(" ", "" ) )

					#	first layerheigth
					if first_h[hit_sl] != "" and first_h[hit_sl] in handle_:
						meta['first_h'] = re.compile("(=( )?|:( )?|,( )?)").split(handle_)[-1:][0].replace(" ", "" )

					#	estimate printtime
					if time_e[hit_sl] != "" and re.compile(time_e[hit_sl]).match(handle_):
						meta['time_e'] = re.compile("(=( )?|:( )?|,( )?)").split(handle_)[-1:][0]

					#	filamentusage
					if filament[hit_sl] != "" and re.compile(filament[hit_sl]).match(handle_):
						meta['filament'] = re.findall("\d+\.\d+", handle_)[0]
						if len( re.findall("\d+\.\d+m", handle_) ) > 0:
							meta['filament'] = float( meta['filament'].replace("m","") ) * 1000	# well cura is using meters, why not
						else:
							meta['filament'] = float(meta['filament'] )

					#	Full slicername
					if meta['slicer'] in handle_:
						meta['slicer'] = meta['slicer'] + " " + handle_.split(meta['slicer'])[1]

				#	data refining:
				meta['time_e'] = calc_time(meta['time_e'])	#	bring time to seconds

				#	there are slicers doin % as firstlayer
				if meta['first_h'] != -1 and "%" in meta['first_h']:
					meta = calc_first_layer(meta)
				else:
					meta['first_h'] = float(meta['first_h'])
				return meta

			self.gcode_reply.append("Your Slicer is not yet implemented.")
			return {}
		###

		#import pdb; pdb.set_trace()

		#	hits if we are in printing state
		if web_.get_argument('name', default=None) is not None:
			path_ = self.sdpath + web_.get_argument('name').replace("0:", "")
			self.cached_file_info = None
		else:
			path_ = self.sdcard.current_file.name
			if self.cached_file_info is not None:
				self.cached_file_info['printDuration'] = self.toolhead.print_time
				return self.cached_file_info

		#	klipper hates subpath for sdcardprints.
		if "/gcodes" in path_:
			path_ = path_.replace("/gcodes", "")

		if not os.path.isfile(path_):
			repl_ = { "err": 1 }

		meta_ = read_gcode(path_)

		#	setup reply with correct datatypes
		repl_ = {
			"err": int(0) ,
			"size": int(os.stat(path_).st_size) ,
			"lastModified": str(datetime.datetime.utcfromtimestamp( os.stat(path_).st_mtime ).strftime("%Y-%m-%dT%H:%M:%S")) ,
			"height": float( meta_.get("objects_h",1 ) ) ,
			"firstLayerHeight": meta_.get("first_h",1 ) ,
			"layerHeight": float( meta_.get("layer_h",1) ) ,
			"printTime": int( meta_.get("time_e",1) ) ,			# in seconds
			"filament": [ float( meta_.get("filament",1) ) ] ,		# in mm
			"generatedBy": str( meta_.get("slicer","<<Slicer not implemented>>") ) ,
			"fileName": web_.get_argument("name",path_.split("/")[-1] ) ,
			"layercount": ( float(meta_.get("objects_h",1)) - meta_.get("first_h",1) ) / float(meta_.get("layer_h",1) ) + 1
		}

		self.cached_file_info = repl_

		return repl_

	#	dwc rr_gcode - append to gcode_queue
	def rr_gcode(self, g_):
		#	took from Fheilman paneldue
		self.gcode_queue.append(g_)
		self.reactor.register_callback(self.send_gcode)
		return {"err": 0}

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
	
	# 	rr_status_0 if klipper is down/failed to start
	def rr_status_0(self):

		#	just put in things really needed to make dwc2 happy

		repl_ = {
			"status": self.get_printer_status(0),
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
			"mountedVolumes": 1
		}

		return repl_

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

		repl_ = {
			"status": self.get_printer_status(now),
			"coords": {
				"axesHomed": self.get_axes_homed(),
				"xyz": self.toolhead.get_position()[:3] ,
				"machine": [ 0, 0, 0 ],			#	what ever this is? no documentation.
				"extr": self.toolhead.get_position()[3:]
			},
			"speeds": {
				"requested": 0,
				"top": gcode_stats['speed']	/ 60	#	not ecxatly the same but comes close
			},
			"currentTool": 0,
			"params": {
				"atxPower": 0,
				"fanPercent": [ fan_['speed']*100 for fan_ in fan_stats ] + [ 0 for missing_ in range( 0, 9 - len(fan_stats) ) ] ,
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
				"chamber": {
                    "active"  : 25,
                    "heater"  : 3,
                },
				"current": [ bed_stats['actual'] ] + [ ex_['actual'] for ex_ in extr_stat ] + [ 0 for missing_ in range( 0, 7 - len(extr_stat) ) ] ,
				"state": [ bed_stats['state'] ] + [ ex_['state'] for ex_ in extr_stat ] + [ 0 for missing_ in range( 0, 7 - len(extr_stat) ) ],
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
			"time": self.start_time - time.time()
		}

		return repl_

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
		repl_ = {
			"status": self.get_printer_status(now) ,
			"coords": {
				"axesHomed": self.get_axes_homed() ,
				"xyz": self.toolhead.get_position()[:3] ,
				"machine": [ 0, 0, 0 ] ,
				"extr": self.toolhead.get_position()[3:]
			},
			"speeds": {
				"requested": 0 ,
				"top": gcode_stats['speed']	/ 60	#	not ecxatly the same but comes close
			},
			"currentTool": -1 ,
			"params": {
				"atxPower": 0 ,
				"fanPercent": [ fan_['speed']*100 for fan_ in fan_stats ] + [ 0 for missing_ in range( 0, 9 - len(fan_stats) ) ] ,
				"fanNames": [ "", "", "", "", "", "", "", "", "" ],
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
				"current": [ bed_stats['actual'] ] + [ ex_['actual'] for ex_ in extr_stat ] + [ 0 for missing_ in range( 0, 7 - len(extr_stat) ) ] ,
				"state": [ bed_stats['state'] ] + [ ex_['state'] for ex_ in extr_stat ] + [ 0 for missing_ in range( 0, 7 - len(extr_stat) ) ],
				"names": [ "", "", "", "", "", "", "", "" ],
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
			"time": self.start_time - time.time(),
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
			"name": "Schampus Michel",
			"probe": {
				"threshold": 100,
				"height": 0,
				"type": 8
			},
			#	will work for 1 extruder
			#[ 0 ] for ex_ in extr_stat
			"tools": [
				{
					"number": extr_stat.index(ex_) + 1 ,
					"heaters": [ extr_stat.index(ex_) + 1 ],
					"drives": [	extr_stat.index(ex_) ] ,
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
		}

		return repl_

	#	dwc rr_status 3
	def rr_status_3(self):
		#	nested here as its related to fileinfo_3 only
		def manage_print_data():

			#	init print data on started print
			if not self.print_data:

				lz_ = gcode_stats['last_zpos']

				self.print_data = {
					"print_start": time.time() ,
					"print_dur": 0 ,
					"extr_start": [ ex_['pos'] for ex_ in extr_stat ] ,
					"firstlayer_dur": 0 ,
					"curr_layer": 1 ,
					"curr_layer_start": 0 ,
					"curr_layer_dur" : 0 ,
					"heat_time": 0 ,
					"zhop": False ,
					"last_zposes":	[ lz_ for n_ in range(6) ]#	takes care of zhops
				}

			self.z_mean = round( sum(self.print_data['last_zposes']) / len(self.print_data['last_zposes']) , 2 )

			if self.print_data['curr_layer_start'] == 0 \
					and sum(self.print_data['extr_start']) < sum([ ex_['pos'] for ex_ in extr_stat ]):
				#	now we know firstlayer started + heating ended(homing?)
				self.print_data['curr_layer_start'] = time.time()
				self.print_data['heat_time'] = time.time() - self.print_data['print_start']

			if self.z_mean < gcode_stats['last_zpos']:
				# curr zpos raised
				self.print_data['zhop'] = True
			elif self.z_mean > gcode_stats['last_zpos']:
				# curr zpos is now lower as history mean so it was a travel zhop
				self.print_data['zhop'] = False
			if self.z_mean == gcode_stats['last_zpos'] \
					and self.print_data['zhop']:
				# now we know layer switched
				#logging.info( "CHANGELAYER" + str(self.z_mean) )
				#self.print_data['last_zposes'] = [ gcode_stats['last_zpos'] for x in self.print_data['last_zposes'] ]
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

		try:
			repl_ = {
				"status": self.get_printer_status(now) ,
				"coords": {
					"axesHomed": self.get_axes_homed() ,
					"xyz": self.toolhead.get_position()[:3] ,
					"machine": [ 0, 0, 0 ] ,
					"extr": self.toolhead.get_position()[3:]
				},
				"speeds": {
					"requested": 0 ,
					"top": gcode_stats['speed'] /60	#	not ecxatly the same but comes close
				},
				"currentTool": -1 ,
				"params": {
					"atxPower": 0 ,
					"fanPercent": [ fan_['speed']*100 for fan_ in fan_stats ] + [ 0 for missing_ in range( 0, 9 - len(fan_stats) ) ] ,
					"fanNames": [ "", "", "", "", "", "", "", "", "" ],
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
					"current": [ bed_stats['actual'] ] + [ ex_['actual'] for ex_ in extr_stat ] + [ 0 for missing_ in range( 0, 7 - len(extr_stat) ) ] ,
					"state": [ bed_stats['state'] ] + [ ex_['state'] for ex_ in extr_stat ] + [ 0 for missing_ in range( 0, 7 - len(extr_stat) ) ],
					"names": [ "", "", "", "", "", "", "", "" ],
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
				"time": self.start_time - time.time(),
				"currentLayer": self.print_data['curr_layer'] ,
				"currentLayerTime": self.print_data['curr_layer_dur'],
				"extrRaw": self.toolhead.get_position()[3:] ,
				"fractionPrinted": self.sdcard.get_status(now) , # percent done
				"filePosition": self.sdcard.file_position,
				"firstLayerDuration": self.print_data['firstlayer_dur'] if self.print_data['firstlayer_dur'] > 0 else self.print_data['curr_layer_dur'],
				"firstLayerHeight": self.cached_file_info['firstLayerHeight'],
				"printDuration": self.print_data['print_dur'] ,
				"warmUpDuration": self.print_data['heat_time'],
				"timesLeft": {
					"file": (1-self.sdcard.get_status(now)['progress']) * self.cached_file_info['printTime'],
					"filament": (1-( sum(self.toolhead.get_position()[3:]) - sum(self.print_data["extr_start"])  ) \
									/ sum(self.cached_file_info["filament"]) ) * self.cached_file_info['printTime'],
					"layer": (1-self.print_data['curr_layer'] / self.cached_file_info['layercount']) * self.cached_file_info['printTime']
				}
			}

		except Exception as e:
			import pdb; pdb.set_trace()

		return repl_

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

		#	klipper hates subpath for sdcardprints.
		if "/gcodes" in path_:
			path_ = path_.replace("/gcodes", "")

		with open(path_, 'w') as out:
			out.write(web_.request.body)
			ret_ = {"err":0}

		return ret_

#
#
#
	#	rrf G10 command - set heaterstemp
	def cmd_G10(self, params):

		tool = params['P']
		temp = max(self.gcode.get_float('S', params, 0.), self.gcode.get_float('R', params, 0.))	#	not fully get this - John
		command_ = "M104 T%d S%0.2f" % (int(tool)-1,float(temp))
		return command_

	#	rrf M0 - cancel print from sd
	def cmd_M0(self, params):

		self.sdcard.must_pause_work = True 		#	pause print -> sdcard postion is saved in virtual sdcard
		self.sdcard.file_position = 0			#	reset fileposition
		self.sdcard.work_timer = None 			#	reset worktimer
		self.sdcard.must_pause_work = False 	#	this is for our ugy getstatus

		#	let user define a cancelprint macro`?
		return 0
	#	rrf M32 - start print from sdcard
	def cmd_M32(self, params):

		#	replace the shit with regx - this will fail in its current state someday
		file = params.get('#original').split("/")[-1].split(" ")[-1].replace("\"", "")
		command = "M23 " + file
		command += "\nM24"

		self.print_data = None

		return command

	#	rrf run macro
	def cmd_M98(self, params):

		# Execute file, used for macro execution

		path = self.sdpath + "/" + params['#original'].split(" ")[1].replace("P\"0:/", "").replace("\"", "")

		if not os.path.exists(path):
			#	now we know its no macro file
			klipma = params['#original'].split("/")[2].replace("\"", "")
			self.gcode_queue.append(klipma)
			return 0

		else:
			#	now we know its a macro from dwc
			with open( path ) as f:
				lines = f.readlines()

			for com_ in [x.strip() for x in lines]:
				self.gcode_queue.append( com_ )

			return 0

	#	rrf M106 translation to klipper scale
	def cmd_M106(self, params):

		if float(params['S']) < .05:
			command = str("M107")
		else:
			command = params['#command'] + " S" + str(int( float(params['S']) * 255 ))

		return str(command)

	#	fo ecxecuting m112 imidiatly!
	def cmd_M112(self, params):
		self.cmd_M0(params)
		self.printer.invoke_shutdown("Emergency Stop from DWC 2")

	#	setting babysteps:
	def cmd_M290(self, params):

		if self.get_axes_homed()[2] == 0:
			self.gcode_reply.append("!! Only idiots try to babystep withoung homing !!")
			return 0

		mm_step = self.gcode.get_float('Z', params)
		params = self.parse_params("SET_GCODE_OFFSET Z_ADJUST%0.2f" % mm_step)
		self.gcode.cmd_SET_GCODE_OFFSET(params)
		self.gcode_reply.append("Z adjusted by %0.2f" % mm_step)

		return 0

	#	rrf restart command
	def cmd_M999(self, params):

		return [ "FIRMWARE_RESTART", "RESTART" ]

	#	sending gcode to klippy
	def send_gcode(self, eventtime):

		#	special cas klipper not ready/shutdown / mcu failure whatever
		if not self.klipper_ready:

			basic_allow = [ "STATUS", "RESTART", "FIRMWARE_RESTART" ]
			while self.gcode_queue:
				command = self.gcode_queue.pop(0).replace(" \"0:","").replace("M999", "RESTART\nFIRMWARE_RESTART")
				if command in basic_allow:
					self.gcode.process_commands( [command] )
				else:
					self.gcode_reply.append("!! Command >> %s << can not run as klipper is not ready !!" % command )

		now = self.reactor.monotonic()
		commands = []

		for com_ in self.gcode_queue :

			# cover emergencys just do it now!!
			if len([ True for s in self.gcode_queue if "M112".lower() in s.lower() ]) > 0:
				self.cmd_M112("1")

			command = com_.replace(" \"0:","")
			params = self.parse_params(command)
			params['#command'] = params['#command'].split(" ")[0]

			rrf_commands = {
				"G10": self.cmd_G10 ,		#	set heaters temp
				"M0": self.cmd_M0 ,			#	cancel SD print
				"M32": self.cmd_M32 ,		#	Start sdprint
				"M98": self.cmd_M98 ,		#	run macro
				"M106": self.cmd_M106 ,		#	set fan
				"M290": self.cmd_M290 ,		#	set babysteps
				"M999": self.cmd_M999		#	issue restart
			}

			#	filter crap and implement em step by step. 
			supported_gcode = [ 
				"G0" , "G1", "G10", "G28", "G90", "G91", "M0", "M24", "M25", "M32", "M83", "M98", "M106", "M112", "M114", "M119", "M140", "M220",
				"M221", "M290", "M999", "FIRMWARE_RESTART", "QUAD_GANTRY_LEVEL", "RESTART", "STATUS" ]

			#	midprint ecxecutions directly to klippy
			mid_print_allow = {
				"M0": self.cmd_M0 ,				#	cancel SD print
				"M24": self.sdcard.cmd_M24 ,					#	start or resume sdprint
				"M25": self.sdcard.cmd_M25 ,					#	Pause SDprint
				"M106": self.gcode.cmd_M106 ,					#	set fanspeed
				"M112": self.cmd_M112 ,							#	emergency stop
				"M220": self.gcode.cmd_M220	,					#	set speedfactor
				"M221": self.gcode.cmd_M221 ,					#	set extrudefactor
				"M290": self.cmd_M290							#	set Babystep
			}

			#	handle unsupported commands
			if params['#command'].upper() not in supported_gcode and params['#command'] in self.klipper_macros:
				self.gcode_reply.append("!! Command >> %s << is not supported !!" % params['#original'])
				import pdb; pdb.set_trace()
				continue

			#	if we are midprint, do it directly to klippers object without gcode_queue
			if self.get_printer_status(now) == "P":
				func_ = mid_print_allow.get(params['#command'])
				if func_ is not None:
					func_(params)
					continue
				else:
					self.gcode_reply.append("!! Command >> %s << is not allowed during print !!" % params['#command'])
					logging.info( json.dumps(params) )
					continue

			#	handle rrfs specials
			if params['#command'] in rrf_commands.keys():
				func_ = rrf_commands.get(params['#command'])
				command = func_(params)
				if command == 0:
					continue

			if type(command) == str:
				appendors = [c for c in command.split("\n")]
			elif type(command) == list:
				appendors = command
			else:
				logging.error( "DWC2 - Error in commandtype " + type(command) )
				continue

			for c in appendors:
				commands.append( c )

		if commands:
			logging.info( "DWC2 - sending gcode: " + json.dumps( commands ) )
			self.gcode_queue = []
			self.gcode.process_commands( commands )

		#import pdb; pdb.set_trace()

	#	getting response by callback
	def gcode_response(self, msg):

		#	we will parse it later here
		logging.debug( "DWC2 DEBUG - incomming gcode_reply: " + str( msg ) )
		self.gcode_reply.append(msg)

		#import pdb; pdb.set_trace()

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

	#	
	#	datafetching for dicts mostly taken from Fheilman
	#
	#	return status for infoblock parts taken from Fheilmann
	def get_printer_status(self, now):

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

		if self.sdcard is not None:
			if self.sdcard.must_pause_work:
				# D = pausing, A = paused
				return "D" if self.sdcard.work_timer is not None else "S"	#	A is not pause
			if self.sdcard.current_file is not None and self.sdcard.work_timer is not None:
				# Printing
				return "P"

		if self.gcode.get_status(now)['busy']:
			# B = busy
			return "B"

		return "I"

	#	import klipper macros as virtual files
	def get_klipper_macros(self):

		for key_ in self.gcode.gcode_help.keys():

			if self.gcode.gcode_help[key_] == "G-Code macro":

				self.klipper_macros.append( key_.lower() )

	def get_axes_homed(self):

		kin = self.toolhead.get_kinematics()

		if not kin.limits:
			return [ 0.,0.,0. ]
		homed = []
		for axis in 'XYZ':
			index = self.gcode.axis2pos[axis]
			homed.append(0 if kin.limits[index][0] > kin.limits[index][1] else 1)
		return homed

	def get_extr_stats(self, now):

		# position - self.extruders[0].extrude_pos
		# temps - self.extruders[0].heater.get_status(self.reactor.monotonic()) - {'temperature': 21.225501996279693, 'target': 0.0}
		# temps - self.extruders[0].heater.stats(self.reactor.monotonic())
		extr_stats = []

		for ex_ in self.extruders:

			if ex_ is not None:

				status = ex_.heater.get_status(now)

				app_ = {
					'pos': ex_.extrude_pos ,
					'actual': status['temperature'] ,
					'target': status['target'] ,
					'state': 0 if status['target'] < 20 else 2 ,
					'min_extrude_temp': ex_.heater.min_extrude_temp ,
					'max_temp': ex_.heater.max_temp
				}

				extr_stats.append( app_ )
		return extr_stats

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

def load_config(config):
	return web_dwc2(config)
