# -*- coding: utf-8 -*-
# vim:set expandtab tabstop=4 shiftwidth=4:
################################################################################
#
# RRDGraph Python RRD Graphing library
# Copyright (C) 2007-2011 CS-SI
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
################################################################################
# $Id$

"""
Composant Python pour afficher les données contenues dans des fichiers RRD
"""

import rrdtool
import os
import glob
import time
import calendar
import datetime
import csv
import locale
import copy
from cStringIO import StringIO

from logging import getLogger
LOGGER = getLogger(__name__)

from tg import config, request
from pylons.i18n import ugettext as _
from paste.deploy.converters import asbool

from vigilo.common import get_rrd_path

from vigirrd.lib import conffile
from vigirrd.model import DBSession, Host, Graph, PerfDataSource
from vigirrd.model.secondary_tables import GRAPH_PERFDATASOURCE_TABLE


def dateToDateObj(date):
    """
    Convertit une date au format "AAAA-MM-JJ" en objet date de Python.

    @param date: Date au format "AAAA-MM-JJ".
    @type date: C{basestring}
    @return: Objet Python pour représenter la date.
    @rtype: C{datetime.datetime}
    """
    year = int(date[0:4])
    month = int(date[5:7])
    day = int(date[8:10])
    dateobj = datetime.date(year, month, day)
    return dateobj

def dateToTimestamp(date):
    """
    Transforme une date au format "AAAA-MM-JJ" en timestamp UNIX.

    @param date: Date au format "AAAA-MM-JJ".
    @type date: C{basestring}
    @return: Timestamp UNIX équivalent à la date donnée.
    @rtype: C{int}
    """
    return time.mktime(dateToDateObj(date).timetuple())

def listFiles(host):
    """
    List the relevant RRD files for the specified host during the \
    specified time
    """
    files = []
    rrd_base_dir = config['rrd_base']
    rrd_path_mode = config.get("rrd_path_mode", "flat")
    host_path = get_rrd_path(host, base_dir=rrd_base_dir,
                             path_mode=rrd_path_mode)
    rrd_pattern = os.path.join(host_path, '*.rrd')
    LOGGER.debug(rrd_pattern)
    files.extend(glob.glob(rrd_pattern))
    files.sort()
    return files

def getStartTime(host):
    """Returns the timestamp of the first available data for the given host"""
    files = listFiles(host)
    for file_l in files:
        rrd = RRD(file_l)
        try:
            return rrd.getStartTime()
        except RRDError:
            continue
    raise RRDError("Can't find the first timestamp available "
                    "for host %s." % host)

def listDS(files):
    """
    Renvoie la liste des sources de données contenues
    dans une série de fichiers RRD.

    @param files: Liste de fichiers RRD à analyser.
    @type files: C{list} of C{basestring}
    @return: Listes des noms des sources de données disponibles
        en analysant les fichiers RRD indiqués.
    @rtype: C{list} of C{str}
    """
    rrds = map(RRD, files)
    list_l = []
    for rrd in rrds:
        for ds in rrd.listDS():
            if ds in list_l:
                continue
            else:
                list_l.append(ds)
    list_l.sort()
    return list_l

def showMergedRRDs(server, template_name, outfile='-',
        start=0, duration=0, details=1):
    """showMergedRRDs"""
    host = Host.by_name(server)
    if host is None:
        raise RRDNotFoundError, server
    graph = Graph.by_host_and_name(host, template_name)
    if graph is None:
        LOGGER.error("ERROR: The template '%(template)s' does not exist. "
                    "Available templates: %(available)s", {
            'template': template_name,
            'available': ", ".join([g.name for g in host.graphs]),
        })

    # Copie en profondeur pour éviter des écrasements
    # de valeurs entre threads (ticket #549).
    template = copy.deepcopy(conffile.templates[graph.template])
    template["name"] = template_name
    template["vlabel"] = graph.vlabel
    template["last_is_max"] = graph.lastismax
    ds_map = {}
    ds_list = graph.perfdatasources
    for ds in ds_list:
        ds_map[ds.name] = getEncodedFileName(server, ds.name)
    tmp_rrd = RRD(filename=ds_map, server=server)
    tmp_rrd.graph(template, ds_list, outfile, start=start, \
                  duration=duration, details=(int(details)==1))

def getEncodedFileName(server, ds):
    """
    Étant donné un nom d'hôte et un indicateur sur cet hôte,
    renvoie le nom du fichier qui contient les informations
    sur cet hôte et cet indicateur.

    @param server: Nom de l'hôte dont les données de métrologie
        nous intéressent.
    @type server: C{basestring}
    @param ds: Nom de l'indicateur qui nous intéresse
        sur l'hôte.
    @type ds: C{basestring}
    @return: Nom du fichier contenant les informations sur
        cet hôte/indicateur.
    @rtype: C{str}
    """
    rrd_base_dir = config['rrd_base']
    rrd_path_mode = config.get("rrd_path_mode", "flat")
    filename = get_rrd_path(server, ds, base_dir=rrd_base_dir,
                            path_mode=rrd_path_mode)
    return filename

# VIGILO_EXIG_VIGILO_PERF_0040:Export des donnees d'un graphe au format CSV
def exportCSV(server, graphtemplate, ds, start, end):
    """
    export CSV

    # valeurs finales -> dictionnaire
    # - renseigné a partir dictionnaires obtenus pour chaque indicateur
    # - sous la forme :
    #   * cle = indice
    #   * valeur = [TimeStamp, donnee dictionnaire1 pour TimeStamp, ...,
    #                donnee dictionnaireN pour TimeStamp
    @param server : serveur
    @type server : C{str}
    @param graphtemplate : graphe
    @type graphtemplate : C{str}
    @param ds : indicateur graphe
    @type ds : C{str}
    @param start : debut plage export
    @type start : C{str}
    @param end : fin plage export
    @type end : C{str}
    @return : donnees RRD d apres server, indicateur et plage
    @rtype: dictionnaire
    """

    # initialisation
    host = Host.by_name(server)
    graph = Graph.by_host_and_name(host, graphtemplate)

    # Si l'indicateur est All ou n'existe pas pour cet hôte,
    # tous les indicateurs sont pris en compte.
    if not ds or ds.lower() == "all":
        ds_list = [pds.name for pds in graph.perfdatasources]
    elif isinstance(ds, basestring):
        ds_list = [ds]

    ds_map = {}
    for ds in ds_list:
        ds_map[ds] = getEncodedFileName(server, ds)

    headers = ['Timestamp']
    headers.extend(ds_list)

    # Construction d'une liste de listes contenant un horodatage
    # et les valeurs des différents indicateurs sélectionnés.
    # L'horodatage arrive toujours en premier. Ensuite, les valeurs
    # arrivent dans l'ordre des indicateurs donnés dans ds_list.
    result = []
    format_date = config.get('csv_date_format')
    format_value = asbool(config.get('csv_respect_locale', False))

    # Au cas où la configuration serait changée dynamiquement.
    if not format_value:
        locale.setlocale(locale.LC_NUMERIC, 'C')
        locale.setlocale(locale.LC_TIME, 'C')
    else:
        try:
            lang = request.accept_language.best_matches()
        except TypeError:
            # Lorsque le thread n'a pas de "request" associée
            # (ex: dans les tests unitaires), TypeError est levée.
            lang = []

        for tentative_lang in lang:
            try:
                tentative_lang = tentative_lang.replace('-', '_')
                # Pour le formattage des nombres.
                locale.setlocale(locale.LC_NUMERIC, tentative_lang)
                # Pour le formattage des dates/heures.
                locale.setlocale(locale.LC_TIME, tentative_lang)
            except locale.Error:
                pass
            else:
                LOGGER.debug(u"Preparing to format CSV values, "
                            "using locale %r" % tentative_lang)
                break

    for ds in ds_list:
        tmp_rrd = RRD(filename=ds_map[ds], server=server)
        # Récupère les données de l'indicateur sous la forme suivante :
        # dict(timestamp1=[valeur1], timestamp2=[valeur2], ...)
        # Les valeurs sont des listes car un fichier peut potentiellement
        # contenir plusieurs indicateurs. Ce n'est pas le cas sur Vigilo
        # où la liste de valeurs contiendra TOUJOURS une seule valeur.
        values_ind = tmp_rrd.fetchData(
            ds_names=['DS'], start_a=start, end_a=end)

        for index, timestamp in enumerate(values_ind):
            # Si l'index n'existe pas encore dans le résultat,
            # c'est qu'il s'agit du premier indicateur que l'on traite.
            # On ajoute donc le timestamp à la liste avant tout.
            if len(result) <= index:
                # Le timestamp est ajouté, une première fois pour trier
                # les valeurs ensuite, une seconde fois (potentiellement
                # formatté) selon le choix de l'administrateur.
                if format_date is None:
                    result.append([timestamp, timestamp])
                else:
                    date = datetime.datetime.utcfromtimestamp(timestamp
                        ).strftime(format_date.encode('utf8')).decode('utf8')
                    result.append([timestamp, date])

            # On prépare le format adéquat en fonction du type de la valeur.
            # Le formattage tiendra compte de la locale.
            # Exemples pour le français :
            #   int(1234) -> "1 234"
            #   float(1234.56) -> "1234,56"
            format_for_value = isinstance(values_ind[timestamp][0], int) and \
                '%d' or '%f'
            value = (values_ind[timestamp][0] is not None) and \
                locale.format(format_for_value, values_ind[timestamp][0]) or \
                None
            result[index].append(value)

    # On trie les valeurs par timestamp ascendant
    # et on supprime la 1ère valeur de chaque entrée
    # qui n'a servi qu'au tri.
    result = sorted(result, key=lambda r: int(r[0]))
    result = [r[1:] for r in result]

    buf = StringIO()
    csv_writer = csv.writer(buf,
        delimiter=config.get("csv_delimiter_char", ';'),
        escapechar=config.get("csv_escape_char", '\\'),
        quotechar=config.get("csv_quote_char", '"'),
        quoting=csv.QUOTE_ALL)

    # Écriture de l'en-tête, puis des données.
    csv_writer.writerow(headers)
    csv_writer.writerows(result)
    return buf.getvalue()

def getExportFileName(host, ds_graph, start, end):
    """
    Determination nom fichier pour export
    -> <hote>_<ds_graph>_<date_heure_debut>_<date_heure_fin>
    avec format <date_heure_...> = AAMMJJ-hhmmss

    @param host : hôte
    @type host : C{str}
    @param ds_graph : indicateur graphe (nom du graphe ou d'un des indicateurs)
    @type ds_graph : C{str}
    @param start : date-heure de debut des donnees
    @type start : C{str}
    @param end : duree des donnees
    @type end : C{str}

    @return: nom du fichier
    @rtype: C{str}
    """

    # plage temps sous forme texte
    format = '%Y/%m/%d-%H:%M:%S'

    dt = datetime.datetime.utcfromtimestamp(int(start))
    str_start = dt.strftime(format)

    dt = datetime.datetime.utcfromtimestamp(int(end))
    str_end = dt.strftime(format)

    host = host.encode('utf-8', 'replace')
    ds_graph = ds_graph.encode('utf-8', 'replace')

    # nom fichier
    filename = '%s - %s (%s - %s)' % (host, ds_graph, str_start, str_end)
    filename = filename.encode('backslash')

    # extension
    filename += ".csv"

    return filename


class RRDError(Exception):
    pass
class RRDNoDSError(RRDError):
    pass
class RRDNotFoundError(RRDError):
    pass

class RRD(object):
    """An RRD database"""

    def __init__(self, filename=None, server=None):
        self.ds = []
        self.filename = filename
        self.server = server
        if server is not None:
            self.host = Host.by_name(server)

    def getGraphs(self):
        """Gets templated graphes"""
        return self.host.graphs

    def getStep(self):
        """Get step from the RRD"""
        return rrdtool.info(self.filename)["step"]

    def getLast(self):
        """getLast"""
        return rrdtool.last(str(self.filename))

    def getStartTime(self):
        """Gets the timestamp of the first non-null entry in the RRD"""
        first =  rrdtool.first(self.filename)
        end = rrdtool.last(self.filename)
        try:
            info , _ds_rrd , data = rrdtool.fetch(self.filename,
                    "AVERAGE", "--start", str(first), "--end", str(end))
        except rrdtool.error:
            # Adjust for daylight saving times
            first = first - 3600
            end = end + 3600
            info , _ds_rrd , data = rrdtool.fetch(self.filename,
                    "AVERAGE", "--start", str(first), "--end", str(end))
        #start_rrd = info[0]
        #end_rrd = info[1]
        step = info[2]
        for line in data:
            all_none = True
            for value in line:
                if value is not None:
                    all_none = False
            if not all_none:
                break
            first = first + step
        if first >= end:
            raise RRDError("The RRD file looks empty !")
        return first

    def getStartTimePath(self):
        """Returns the start timestamp of the RRD, based on its path.
           It is not necessarily the timestamp of the first entry.
           Unused at the moment."""
        dir_l = os.path.dirname(self.filename)
        hour = os.path.basename(dir_l).rstrip('Z')
        day = os.path.basename(os.path.dirname(dir_l))
        t_tuple = time.strptime("%s %s:00:00 UTC"%(day, hour), \
        "%Y-%m-%d %H:%M:%S %Z")
        t_stamp = calendar.timegm(t_tuple)
        start = t_stamp - 1
        return start

    def fetchData(self, ds_names, start_a=None, end_a=None):
        """
        Gets the data in the RRD, for the specified DataSources,
        for the optional timeslot
        """
        if not os.path.exists(self.filename):
            raise RRDNotFoundError(self.filename)

        res = {}
        if start_a is None:
            try:
                start = self.getStartTime() - 1
            except RRDError:
                # RRD is empty
                return res
        else:
            start = int(start_a)

        now = int(time.time())
        if start >= now:
            # No data yet
            LOGGER.debug("No data yet")
            return res

        if end_a is not None:
            end = int(end_a)
        else:
            # one hour plus one second, start should be 1 sec in the past
            end = start + 3600 + 1

        if end > now:
            LOGGER.debug("truncating %(filename)s to %(now)d "
                "instead of %(end)d", {
                    'filename': self.filename,
                    'now': now,
                    'end': end,
                })
            end = now
        LOGGER.debug("%s AVERAGE --start %s --end %s" % \
                        (self.filename, str(start), str(end)))

        info , ds_rrd , data = rrdtool.fetch(str(self.filename), "AVERAGE", \
                                   "--start", str(start), "--end", str(end))
        #start_rrd = info[0]
        #end_rrd = info[1]
        step = info[2]

        # put the mapping of indexes in the rrd to their place in the final list
        ds_mapping = {}
        for i in range(len(ds_rrd)):
            ds = ds_rrd[i]
            if ds in ds_names:
                ds_mapping[i] = ds_names.index(ds)

        # Create the data dict
        tstamp = start + 1
        for line in data:
            # fill with 'None', len(ds_names) times
            values = [ None for i in ds_names ]
            for i in range(len(line)):
                # we have a mapping for it, so store it
                if ds_mapping.has_key(i):
                    values[ds_mapping[i]] = line[i]
            res[tstamp] = values
            tstamp = tstamp + step
            if tstamp > end - step:
                break

        return res

    def update(self, ds_list, data):
        """Updates the given ds in the RRD with the given values"""
        timestamps = data.keys()
        timestamps.sort()
        LOGGER.debug("feeding data...")
        for timestamp in timestamps:
            values = data[timestamp]
            clean_values = []
            for v in values:
                if v is None:
                    clean_values.append("U")
                else:
                    clean_values.append(str(v))
            args = [self.filename, "-t", ":".join(ds_list)]
            args.append("%s@%s" % (timestamp, ":".join(clean_values)))
            try:
                rrdtool.update(*args)
            except rrdtool.error:
                # Daylight savings
                LOGGER.info("Skipped daylight savings (timestamp = %s)",
                            timestamp)
                continue

    def graph(self, template, ds_list, outfile="-", format='PNG',
              start=0, duration=0, details=True, lazy=True):
        if outfile != "-":
            imgdir = os.path.dirname(outfile)
            if not os.path.exists(imgdir):
                os.makedirs(imgdir)

        # On choisit la langue qui sera utilisée pour le rendu.
        try:
            lang = request.accept_language.best_matches()
        except TypeError:
            # Lorsque le thread n'a pas de "request" associée
            # (ex: dans les tests unitaires), TypeError est levée.
            lang = None

        if lang:
            selected_locale = lang[0].replace('-', '_')
            if "_" not in selected_locale:
                selected_locale = "%s_%s" % (selected_locale,
                                             selected_locale.upper())
            LOGGER.debug(u"Trying to set rrdtool's locale to %s" %
                selected_locale)
            # D'après plusieurs posts sur internet, rrdtool
            # ne fonctionne qu'avec les locales utilisant UTF-8.
            # De plus, LC_ALL a la priorité sur LC_TIME,
            # il faut donc s'en débarrasser.
            if 'LC_ALL' in os.environ:
                del os.environ['LC_ALL']

            # os.environ est utilisé par rrdtool,
            # setlocale() est utilisé par strftime().
            os.environ['LC_TIME'] = selected_locale
            try:
                locale.setlocale(locale.LC_TIME, selected_locale)
            except locale.Error:
                pass # locale non supportée, c'est pas grave

        #step = self.host.step
        #import pprint
        #pprint.pprint(template)

        start_i = int(start)
        if start_i == 0:
            start_i = int((int(time.time())) - 24*3600 - 1)

        duration_i = int(duration)
        if duration_i == 0:
            end_i = int(time.time())
        else:
            end_i = int(start_i + duration_i)
        # Compute the x-axis labels
        duration_i = end_i - start_i

        #if duration_i <= 7 * 3600:
        #    xgrid = "MINUTE:30:HOUR:1:HOUR:1:0:%d/%m %Hh"
        #elif duration_i > 7 * 3600 and duration_i <= 25 * 3600:
        #    xgrid = "HOUR:1:HOUR:6:HOUR:6:0:%d/%m %Hh"
        #elif duration_i > 25 * 3600 and duration_i <= 8 * 24 * 3600:
        #    xgrid = "HOUR:6:DAY:1:DAY:1:0:%d/%m"
        #elif duration_i > 8 * 24 * 3600 and duration_i <= 15 * 24 * 3600:
        #    xgrid = "DAY:1:DAY:2:DAY:2:0:%d/%m"
        #elif duration_i > 15 * 24 * 3600 and duration_i <= 4 * 31  * 24 * 3600:
        #    xgrid = "DAY:5:DAY:10:DAY:10:0:%d/%m"
        #else:
        #    xgrid = "WEEK:2:MONTH:1:MONTH:1:0:%b"
        a = [
                str(outfile),
                #"--step", str(step),
                #"--x-grid", xgrid,
                "--start", str(start_i),
                "--end", str(end_i),
                "--imgformat", format,
        ]
        if template.has_key("options"):
            for option in template["options"]:
                a.append(option)

        a.append("--title")
        if not details:
            a.append(template["name"])
            a.append("--no-legend")
#            a.append("--only-graph")
            a.append("--width")
            a.append(250)
            a.append("--height")
            a.append(64)
        else:
            if len(self.server) > 35:
                ellipsis_server = self.server[:15] + '(...)' + \
                                    self.server[-15:]
            else:
                ellipsis_server = self.server
            a.append("%s: %s" % (ellipsis_server, template["name"]))
            a.append("--width")
            a.append(self.host.width)
            a.append("--height")
            a.append(self.host.height)
            a.append("--vertical-label")
            a.append(template["vlabel"])
            a.append("TEXTALIGN:left")
        if lazy:
            a.append("--lazy")
        # Curve smoothing
        a.append("-E")

        # Calcul du label le plus long
        labels = []
        for d in ds_list:
            label = d.label
            if not label:
                label = d.name
            labels.append(len(label))
        justify = max(labels)

        # Ajoute la date de début et de fin avant la légende, centrées.
        # Le format par défaut est celui le plus adapté à la locale.
        format_date = config.get(
                'graph_date_format',
                locale.nl_langinfo(locale.D_T_FMT)
            ).encode('utf-8')

        # Le caractère ":" est réservé dans rrdtool et doit être échappé.
        # Le rstrip() permet de supprimer un espace présent en trop à la fin
        # du texte, lié à l'absence de fuseau horaire et à la présence du
        # format %Z par défaut dans la plupart des locales.
        start_date = datetime.datetime.utcfromtimestamp(
            start_i).strftime(format_date).replace(':', '\\:').rstrip()
        end_date = datetime.datetime.utcfromtimestamp(
            end_i).strftime(format_date).replace(':', '\\:').rstrip()
        a.append(
            (
                'COMMENT:' + (_('From "%(start)s" to "%(end)s"') % {
                    # @FIXME: l'encodage ne devrait pas être hard-codé.
                    'start': start_date.decode('iso-8859-15'),
                    'end': end_date.decode('iso-8859-15'),
                }) + '\\c'
            ).encode('utf-8')
        )
        a.append("COMMENT:  \\n")

        # Tabs (legend)
        s = "COMMENT:%s" % _("value").ljust(justify+3)
        for tab in template["tabs"]:
            # if we have a nicer label, use it
            if tab in config.static_labels:
                tabstr = config.static_labels[tab]
            else:
                # if we dont, too bad, just print it.
                tabstr = tab
            s += tabstr.strip().center(7) # align it

        a.append(str(s)+"\\n")

        for i, d in enumerate(ds_list):
            if "last_is_max" in template and template["last_is_max"] \
                    and i == len(ds_list)-1:
                is_max = True
            else:
                is_max = False
            a.extend(self.get_graph_cmd_for_ds(d, i, template, is_max, justify))

        # rrdtool.graph() ne sait manipuler que le type <str>.
        a = [str(e) for e in a]
        LOGGER.debug("rrdtool graph '%s'" % "' '".join(a))

        rrdtool.graph(*a)

    def get_graph_cmd_for_ds(self, d, i, template, is_max=False, justify=18):
        cmd = []

        if is_max:
            # C'est le max, on fait juste un trait noir
            params = { "type": "LINE1", "color": "#000000", "stack": False }
        else:
            # If we know how to graph the n-th DS...
            if i < len(template["draw"]):
                # ...use those params...
                params = template["draw"][i]
            else:
                # ...else just use the first params.
                params = template["draw"][0]
        # If we have a nicer label, use it
        label = d.label
        if not label:
            label = d.name
        # Find the required factor
        factor = 1
        if params.has_key("invert") and params["invert"]:
            factor = -1
        if d.factor != 1:
            factor = factor * d.factor

        if isinstance(self.filename, dict):
            rrdfile = self.filename[d.name]
        else:
            rrdfile = self.filename
        dsname = "DS"
        if not os.path.exists(rrdfile):
            raise RRDNotFoundError(rrdfile)

        # Génère un indicateur consolidé "<ds>_orig" correspondant
        # à la valeur moyenne sur la période et le pas considérés.
        cmd.append("DEF:%s_orig=%s:%s:AVERAGE" % (i, rrdfile, dsname))
        # Remplace l'indicateur "<ds>" par la valeur de "<ds>_orig"
        # généré précédemment, en lui appliquant le facteur approprié.
        cmd.append("CDEF:%s=%s_orig,%1.10f,*" % (i, i, factor))

        graphline = "%s:%s%s:%s" % (params["type"], i, params["color"], \
            label.replace('\\', '\\\\').replace(':', '\\:').ljust(justify))
#            LOGGER.debug("params=%s, has_key=%d" %
#                (params, params.has_key("stack")))
        if params.has_key("stack") and params["stack"]:
            graphline += ":STACK"
#                LOGGER.debug("added + :STACK to %s"%graphline)
        cmd.append(graphline)
        if is_max:
            cmd.append("GPRINT:%s:LAST:%s\\n" % (i, "%4.0lf%s"))
        else:
            for tab in template["tabs"]:
                cmd.append("GPRINT:%s:%s:%s" % (i, tab, "%4.0lf%s"))
            cmd[-1] = cmd[-1] + "\\n"
        return cmd

    def getLastValue(self, ds):
        """
        Lecture derniere valeur RRD
        @return: Dernière valeur.
        @rtype: C{str} ou C{None}
        """
        # dernier timestamp et step
        lasttime = self.getLast()
        step = self.getStep()
        if not lasttime or not step:
            return None

        # bornes selon timestamp et step
        start = lasttime - step
        end = lasttime

        # informations
        _info, _ds_rrd, data = rrdtool.fetch(self.filename, "AVERAGE",
                               "--start", str(start), "--end", str(end))

        if len(data) == 0 or len(data[0]) == 0:
            return None

        lastValue = data[0][0]
        if lastValue is None:
            return None

        factor = DBSession.query(
                PerfDataSource.factor
            ).join(
                (GRAPH_PERFDATASOURCE_TABLE,
                    GRAPH_PERFDATASOURCE_TABLE.c.idperfdatasource ==
                    PerfDataSource.idperfdatasource),
                (Graph, Graph.idgraph == GRAPH_PERFDATASOURCE_TABLE.c.idgraph),
            ).filter(PerfDataSource.name == unicode(ds)
            ).filter(Graph.idhost == self.host.idhost
            ).scalar()
        if factor is None:
            factor = 1
        return lastValue * factor
