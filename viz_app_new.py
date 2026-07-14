# -*- coding: utf-8 -*-
from flask import Flask, Response, request
import json, time, threading, sys
sys.path.insert(0, '/home/pi/gen3d')

app = Flask(__name__)
_stop_flag = threading.Event()
_cmd_queue = []
_cmd_lock = threading.Lock()
_printer_ref = [None]

