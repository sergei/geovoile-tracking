import glob
import os
import time
from datetime import datetime, timedelta

from selenium.webdriver import Firefox
from selenium.webdriver.firefox.options import Options
import requests
import json
import xml.etree.ElementTree as ET
from gpxpy.gpx import *
import random
import string
from argparse import ArgumentParser

from shipdict import ShipDict

# Firefox Options
opts = Options()
opts.headless = True
assert opts.headless  # Operating in headless mode


class Merger:
    """
    Concatain tracking data from several geovoile.com tracking site
    """
    def __init__(self):
        """
        constructor creates an ArgumentParser object to implement main interface
        puts resulting args in self.args also creates an empty instance of
        ShipDict for merging incoming data
        """
        parser = ArgumentParser(fromfile_prefix_chars='@')
        parser.add_argument("-v", "--verbose", dest='verbose', default=False,
                            action='store_true',
                            help="Verbose mode")
        parser.add_argument("-s", "--ship", dest='ship_name', default=None,
                            action='store',
                            help="Restrict to ships by that name")
        parser.add_argument("-z", "--gzip", dest='gzip', default=False,
                            action='store_true',
                            help="Store kml output in gzip (KMZ) format")
        parser.add_argument("-u", "--tracker-url", default=None, required=True,
                            help="URL of the tracker website")
        parser.add_argument("-c", "--show-class", default=None, required=False,
                            help="Only include this class")
        parser.add_argument("-f", "--exclude-dnf", default=False,
                            action='store_true',
                            help="Only keep active boats")
        parser.add_argument("-g", "--color_gpx", default=False,
                            action='store_true',
                            help="Add color to GPX tracks")

        parser.add_argument("json_filenames", nargs='*')
        self.args = parser.parse_args()

        # the windows command line is a little lazy with filename expansion
        json_filenames = []
        for json_filename in self.args.json_filenames:
            if '*' not in json_filename:
                json_filenames.append(json_filename)
            else:
                json_filenames.extend(glob.glob(json_filename))
        self.args.json_filenames = json_filenames
  
        self.ship_dict = ShipDict()
        self.gpx = GPX()
        self.gpxx_color = [
            "Black",
            "DarkRed",
            "DarkGreen",
            "DarkYellow",
            "DarkBlue",
            "DarkMagenta",
            "DarkCyan",
            "LightGray",
            "DarkGray",
            "Red",
            "Green",
            "Yellow",
            "Blue",
            "Magenta",
            "Cyan",
            "White"
            ]

        self.roots_url = [self.args.tracker_url]
        self.include_boats = []
        self.include_classes = []
        if self.args.show_class:
            self.include_classes.append(self.args.show_class)

        self.start_lat = None
        self.start_lon = None
        self.finish_lat = None
        self.finish_lon = None

    def main(self):
        self.get_data()
        self.export_as_gpx()
        self.make_qt_vlm_xml()

    def get_data(self):
        """
        Download data from urls and decode them as original data are encrypted
        Use inpage JS to retrieve data's urls and decode them
        Add prefixes to id as id are not unique accross tracking data from different site
        """
        with Firefox(options=opts) as driver:
            for root_url, prefixe in zip(self.roots_url, string.ascii_letters):
                print(f'Opening {root_url} ...')
                driver.get(root_url)
                # Wait for page loading
                # TODO : find a better way to wait until page is fully loaded
                time.sleep(2)
                # Get url from page JS
                config_data_url = root_url + driver.execute_script("return tracker._getRessourceUrl('config')")
                print(f'Retrieving config data from {config_data_url} ...')
                x = requests.get(config_data_url)
                # Decode data usgin page JS
                configdata_xml = driver.execute_script("return new TextDecoder('utf-8').decode(new UInt8Array(arguments[0]))", list(x.content))
                # Parse XML to find boats identification
                root = ET.fromstring(bytes(configdata_xml, 'utf8'))
                build_include_boat_list = len(self.include_boats) == 0 and len(self.include_classes) > 0
                for boat_class in root.findall("./boats/boatclass"):
                    boats = boat_class.findall('boat')

                    if build_include_boat_list:
                        if boat_class.attrib['name'] in self.include_classes:
                            self.include_boats += [boat.attrib['name'] for boat in boats]

                    self.ship_dict.new_boat(
                        [[prefixe + boat.attrib['id'], boat.attrib['name']] for boat in boats])

                # Get boats tracks
                tracks_url = root_url + driver.execute_script("return tracker._getRessourceUrl('tracks')")
                print(f'Retrieving boats tracks from {tracks_url} ...')
                x = requests.get(tracks_url)
                tracks_json = driver.execute_script("return new TextDecoder('utf-8').decode(new UInt8Array(arguments[0]))", list(x.content))
                tracks = json.loads(tracks_json)
                for boat_track in tracks['tracks']:
                    self.ship_dict.add_chunk(boat_track, prefixe=prefixe)

                # Get start and finish marks
                leg = root.find("./leg")
                leg_num = int(leg.attrib['num'])
                run = root.findall('./leg/runs/run')[leg_num - 1]
                self.start_lat = float(run.find('start').attrib['lat'])
                self.start_lon = float(run.find('start').attrib['lng'])
                self.finish_lat = float(run.find('arrival').attrib['lat'])
                self.finish_lon = float(run.find('arrival').attrib['lng'])

        # Trim undesired boats
        boats_to_remove = set()
        filter_by_boat_name = len(self.include_boats) > 0
        last_utc = datetime.utcfromtimestamp(0)
        for boat in self.ship_dict.all_ships():
            # Boats from other classes
            if filter_by_boat_name and boat.name not in self.include_boats:
                boats_to_remove.add(boat)

            # Boats with no tracks
            if len(boat.positions) == 0:
                boats_to_remove.add(boat)
                print(f'Excluding {boat.name} since it has no track')
            else:
                utc = datetime.utcfromtimestamp(boat.positions[-1].timestamp)
                if utc > last_utc:
                    last_utc = utc

        print(f'Tracks finish at {last_utc}')
        # Remove all boats that don't finish at this time
        if self.args.exclude_dnf:
            for boat in self.ship_dict.all_ships():
                if len(boat.positions) > 0:
                    time_behind = last_utc - datetime.utcfromtimestamp(boat.positions[-1].timestamp)
                    if time_behind > timedelta(hours=1):
                        print(f'Excluding {boat.name} as DNF')
                        boats_to_remove.add(boat)

        for boat in boats_to_remove:
            # if boat.ID in self.ship_dict:
            del self.ship_dict[boat.ID]

        print(f'Removed {len(boats_to_remove)} boats')

    def export_as_gpx(self):
        """
        Export to GPX, one GPX for all boat, track name is boat name
        Implement track color Dispalay according to GPX Extension
        """

        gpx_wpts = GPX()
        for boat in self.ship_dict.all_ships():

            # Create small XML tree to pass as GPX extention, first tag not taken into account
            # GPX Extension to define track color
            root_extension = ET.Element('')
            track_extension = ET.SubElement(root_extension, 'gpxx:TrackExtension')
            track_color = ET.SubElement(track_extension, 'gpxx:DisplayColor')

            # Initiate Track
            gpx_track = GPXTrack()
            gpx_track.name = 'past ' + boat.name
            self.gpx.tracks.append(gpx_track)
            track_color.text = self.gpxx_color[random.randrange(len(self.gpxx_color))]
            if self.args.color_gpx:
                gpx_track.extensions = root_extension

            # Initiate TrackSegment
            gpx_segment = GPXTrackSegment()
            gpx_track.segments.append(gpx_segment)
            for position in boat.positions:
                utc = datetime.utcfromtimestamp(position.timestamp)
                gpx_segment.points.append(GPXTrackPoint(position.latitude, position.longitude, time=utc))

            # Add last position of the boat as WPT
            wpt = GPXWaypoint(boat.positions[-1].latitude, boat.positions[-1].longitude, name=boat.name)
            gpx_wpts.waypoints.append(wpt)

        tracks_gpx = 'tracks.gpx'
        with open(tracks_gpx, 'w') as f:
            f.write(self.gpx.to_xml())
            print(f'Created {tracks_gpx}')

        gpx_wpts.waypoints.append(GPXWaypoint(self.start_lat, self.start_lon, name='START'))
        gpx_wpts.waypoints.append(GPXWaypoint(self.finish_lat, self.finish_lon, name='FINISH'))
        wpts_gpx = 'wpts.gpx'
        with open(wpts_gpx, 'w') as f:
            f.write(gpx_wpts.to_xml())
            print(f'Created {wpts_gpx}')

    def make_qt_vlm_xml(self):
        xml_dir = os.getcwd() + os.sep + 'qt_vlm_xml'
        os.makedirs(xml_dir, exist_ok=True)

        runs = ET.Element("runs")
        run = ET.SubElement(runs, "run")
        ET.SubElement(run, "clearAllRoutes").text = 'true'
        # ET.SubElement(run, "closeAllGribs").text = 'true'

        filter_by_boat_name = len(self.include_boats) > 0
        for boat in self.ship_dict.all_ships():
            if filter_by_boat_name and boat.name not in self.include_boats:
                continue
            if len(boat.positions) == 0:
                continue

            run = ET.SubElement(runs, "run")
            ET.SubElement(run, "routingName").text = boat.name
            ET.SubElement(run, "startPoint").text = boat.name
            ET.SubElement(run, "endPoint").text = 'FINISH'
            utc = datetime.utcfromtimestamp(boat.positions[-1].timestamp)
            ET.SubElement(run, "startDate").text = utc.strftime("%m/%d/%Y")
            ET.SubElement(run, "startTime").text = utc.strftime("%H:%M:%S")
            ET.SubElement(run, "multiRouting").text = 'false'
            ET.SubElement(run, "convertToRoute").text = 'true'
            ET.SubElement(run, "autoSimpOptim").text = 'false'

        tree = ET.ElementTree(runs)
        xml_name = xml_dir + os.sep + 'qtvlm_all_routes.xml'
        tree.write(xml_name)
        print(f'Created {xml_name}')


# if this not loaded as part of an `import` statement
if __name__ == '__main__':
    # create a Merger instance
    merger = Merger()
    # send it the main method and transmit this return code right to wait()
    exit(merger.main())
