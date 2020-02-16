from core.common.terminal import ModuleUI
from core.config.globalimport import *
from core.utility.printer import display_messages
from random import randrange
import time,signal,sys
from multiprocessing import Process
from scapy.all import *
from core.common.platforms import Linux
from tabulate import tabulate

PROBE_REQUEST_TYPE = 0
PROBE_REQUEST_SUBTYPE = 4
DOT11_REQUEST_SUBTYPE = 2

class ModPump(ModuleUI):
    """ Scan WiFi networks and detect devices"""
    name = "wifiscan"

    options = {
        "interface": "wlxc83a35cef744",
        "timeout": 10
    }
    completions = list(options.keys())

    def __init__(self, parse_args=None, root=None):
        self.parse_args = parse_args
        self.root = root
        self.name_module = self.name
        self.whitelist = ['00:00:00:00:00:00','ff:ff:ff:ff:ff:ff' ]
        self.set_prompt_modules()
        self.aps = {}
        self.A = []
        self.clients = {}
        self.table_headers_wifi = ["CH", "SSID" ,"BSSID", "RSSI","Privacy", ]
        self.table_headers_STA = ["BSSID", "STATION" ,"PWR","Frames", 'Probe']
        self.table_output = []
        super(ModPump, self).__init__(parse_args=self.parse_args, root=self.root )

    def do_run(self, args):
        print(display_messages('setting interface: {} monitor momde'.format(self.options.get("interface")), info=True))
        self.set_monitor_mode()
        print(display_messages('starting Channel Hopping ', info=True))
        self.p = Process(target = self.channel_hopper, args=(self.options.get("interface"),))
        self.p.daemon = True
        self.p.start()
        print(display_messages('sniffing... ', info=True))
        sniff(iface=self.options.get("interface"), prn=self.sniffAp,
         timeout= None if int(self.options.get("timeout")) == 0 else int(self.options.get("timeout")))
        self.p.terminate()

    def channel_hopper(self, interface):
        while True:
            try:
                channel = randrange(1,10)
                os.system("iw dev %s set channel %d" % (interface, channel))
                time.sleep(1)
            except KeyboardInterrupt:
                break


    def handle_probe(self, pkt):
        if pkt.haslayer(Dot11ProbeReq) and '\x00'.encode() not in pkt[Dot11ProbeReq].info:
            essid = pkt[Dot11ProbeReq].info
        else:
            essid = 'Hidden SSID'
        client = pkt[Dot11].addr2

        if client in self.whitelist or essid in self.whitelist:
            return

        if client not in self.clients:
            self.clients[client] = []

        if essid not in self.clients[client]:
            self.clients[client].append(essid)
            self.aps['(not associated)'] ={}
            self.aps['(not associated)']['STA'] = {'Frames': 1, 'BSSID' : '(not associated)', 
                        'Station': client , 'Probe': essid,
                         'PWR': self.getRSSIPacketClients(pkt)}

    def getRSSIPacket(self, pkt) :
        rssi = -100
        if pkt.haslayer(Dot11) :
            if pkt.type == 0 and pkt.subtype == 8 :
                if pkt.haslayer(Dot11Beacon) or pkt.haslayer(Dot11ProbeResp):
                    rssi = pkt[RadioTap].dBm_AntSignal
        return rssi

    def getRSSIPacketClients(self, pkt):
        rssi = -100
        if pkt.haslayer(RadioTap):
            rssi = pkt[RadioTap].dBm_AntSignal
        return rssi

    def getStationTrackFrame(self, pkt):
        if pkt.haslayer(Dot11) and \
                pkt.getlayer(Dot11).type == DOT11_REQUEST_SUBTYPE \
                    and not pkt.haslayer(EAPOL):

            sender = pkt.getlayer(Dot11).addr2
            receiver = pkt.getlayer(Dot11).addr1
            if sender in self.aps.keys():
                if (Linux.check_is_mac(receiver)):
                    if not receiver in self.whitelist:
                        self.aps[sender]['STA'] = {'Frames': 1, 'BSSID' : sender, 
                        'Station': receiver, 'Probe': '',
                         'PWR': self.getRSSIPacketClients(pkt)}
                    if 'STA' in self.aps[sender]:
                        self.aps[sender]['STA']['Frames'] += 1
                        self.aps[sender]['STA']['PWR'] =  self.getRSSIPacketClients(pkt)

            elif receiver in self.aps.keys():
                if (Linux.check_is_mac(sender)):
                    if not sender in self.whitelist:
                        self.aps[receiver]['STA'] = {'Frames': 1, 'BSSID' : receiver, 
                        'Station': sender ,'Probe': '', 'PWR': self.getRSSIPacketClients(pkt)}
                    if 'STA' in self.aps[receiver]:
                        self.aps[receiver]['STA']['Frames'] += 1
                        self.aps[receiver]['STA']['PWR'] =  self.getRSSIPacketClients(pkt)

    def handle_beacon(self, pkt):
        if not pkt.haslayer(Dot11Elt):
            return

        essid = pkt[Dot11Elt].info if '\x00'.encode() not in pkt[Dot11Elt].info and pkt[Dot11Elt].info != '' else 'Hidden SSID'
        bssid = pkt[Dot11].addr3
        client = pkt[Dot11].addr2
        if client in self.whitelist or essid in self.whitelist or bssid in self.whitelist:
            return

        try:
            channel = int(ord(pkt[Dot11Elt:3].info))
        except:
            channel = 0
        
        rssi = self.getRSSIPacket(pkt)

        p = pkt[Dot11Elt]
        capability = p.sprintf("{Dot11Beacon:%Dot11Beacon.cap%}\
                {Dot11ProbeResp:%Dot11ProbeResp.cap%}")

        crypto = set()
        while isinstance(p, Dot11Elt):
            if p.ID == 48:
                crypto.add("WPA2")
            elif p.ID == 221 and p.info.startswith('\x00P\xf2\x01\x01\x00'.encode()):
                crypto.add("WPA")
            p = p.payload

        if not crypto:
            if 'privacy' in capability:
                crypto.add("WEP")
            else:
                crypto.add("OPN")

        enc = '/'.join(crypto)
        self.aps[bssid] = {"ssid": essid, "channel": channel,
            "capability": capability, "enc" : enc, "rssi": rssi}

    def showDataOutputScan(self):
        os.system("clear")
        self.table_output = []
        self.table_station = []
        for bssid, info in self.aps.items():
            if not '(not associated)' in bssid:
                self.table_output.append([info["channel"], info["ssid"] ,
                    bssid, info["rssi"],info["enc"] ])
        print(tabulate(self.table_output, self.table_headers_wifi, tablefmt="simple"))
        print('\n')
        for bssid, info in self.aps.items():
            if ('STA' in info):
                self.table_station.append([info['STA']['BSSID'], 
                info['STA']['Station'],info['STA']['PWR'],info['STA']['Frames'], info['STA']['Probe']])
        if (len(self.table_station) > 0):
            print(tabulate(self.table_station, self.table_headers_STA, tablefmt="simple"))

    def sniffAp(self, pkt):
        self.getStationTrackFrame(pkt)
        if ( pkt.haslayer(Dot11Beacon) or pkt.haslayer(Dot11ProbeResp) or pkt.haslayer(Dot11ProbeReq)):

            if pkt.type == PROBE_REQUEST_TYPE and pkt.subtype == PROBE_REQUEST_SUBTYPE:
                self.handle_probe(pkt)

            if pkt.haslayer(Dot11Beacon) or pkt.haslayer(Dot11ProbeResp):
                self.handle_beacon(pkt)
            
            self.showDataOutputScan()

    def set_monitor_mode(self):
        if not self.options.get("interface") in Linux.get_interfaces().get("all"):
            print(display_messages("the interface not found!", error=True))
            sys.exit(0)
        os.system("ifconfig {} down".format(self.options.get("interface")))
        os.system("iwconfig {} mode monitor".format(self.options.get("interface")))
        os.system("ifconfig {} up".format(self.options.get("interface")))