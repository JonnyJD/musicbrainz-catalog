import os, sys, time, re
import musicbrainzngs.mbxml as mbxml
import musicbrainzngs.util as mbutil
import musicbrainzngs.musicbrainz as mb
import amazonservices
import coverart
import userprefs
import utils
import extradata
import shutil
from datetime import datetime
from collections import defaultdict
import progressbar
import logging
logging.basicConfig(level=logging.INFO)

overWriteAll = False

# Have to give an identity for musicbrainzngs
mb.set_useragent(
    "python-musicbrainz-ngs-catalog",
    "0.1",
    "https://github.com/rlhelinski/musicbrainz-catalog/",
)

# Get the XML parsing exceptions to catch. The behavior chnaged with Python 2.7
# and ElementTree 1.3.
import xml.etree.ElementTree as etree
from xml.parsers import expat
if hasattr(etree, 'ParseError'):
    ETREE_EXCEPTIONS = (etree.ParseError, expat.ExpatError)
else:
    ETREE_EXCEPTIONS = (expat.ExpatError)

def releaseSortCmp(a, b):
    return unicode.lower(a[1]) < unicode.lower(b[1])

import HTMLParser
h = HTMLParser.HTMLParser()

def chunks(l, n):
    """ Yield successive n-sized chunks from l. """
    for i in xrange(0, len(l), n):
        yield l[i:i+n]

def getFormatFromUri(uriStr, escape=True):
    # TODO deprecate
    #return uriStr.split("#")[1].decode('ascii')
    formatStr = uriStr.split("#", 1)[1]
    if escape:
        return h.unescape(formatStr)
    else:
        return formatStr

def getReleaseId(releaseId):
    """Should be renamed to get releaseIdFromInput or something"""
    if releaseId.startswith('http'):
        return utils.extractUuid(releaseId, 'release')
    else:
        return releaseId

class ReleaseFormat(object):
    def __init__(self, fmtStr=""):
        self.fmtStr = fmtStr

    def isVinyl(self):
        return self.fmtStr.endswith('Vinyl') or \
            self.fmtStr.startswith('7"') or \
            self.fmtStr.startswith('10"') or \
            self.fmtStr.startswith('12"')

    def isCD(self):
        # There are many CDs labeled as '(unknown)' type
        return self.fmtStr.endswith('CD') or not self.isVinyl()

    def isOther(self):
        return (not self.isVinyl()) and (not self.isCD())

    def isAny(self):
        return self.fmtStr == ""

    def __eq__(self, other):
        return (self.isVinyl() and other.isVinyl()) or \
                (self.isCD() and other.isCD()) or \
                (self.isAny() or other.isAny())

    def __str__(self):
        return self.fmtStr


class Catalog(object):
    mbUrl = 'http://musicbrainz.org/'
    artistUrl = mbUrl+'artist/'
    labelUrl = mbUrl+'label/'
    releaseUrl = mbUrl+'release/'

    def __init__(self, rootPath='release-id'):
        self.rootPath = rootPath
        if not os.path.isdir(self.rootPath):
            os.mkdir(self.rootPath)
        self.prefs = userprefs.PrefManager()
            
    def _get_xml_path(self, releaseId, fileName='metadata.xml'):
        return os.path.join(self.rootPath, releaseId, fileName)

    def renameRelease(self, releaseId, newReleaseId):
        os.rename(os.path.join('release-id', releaseId),
                os.path.join('release-id', newReleaseId) )
        # TODO this is lazy, but it is the only way to correct all the tables created during load()
        self.load()
        self.refreshMetaData(newReleaseId, olderThan=60)

    def getReleaseIds(self):
        return self.metaIndex.keys()

    # TODO rename metaIndex back to releaseIndex or relIndex
    def getRelease(self, releaseId):
        return self.metaIndex[releaseId]

    def getReleases(self):
        for releaseId, metadata in self.metaIndex.items():
            yield releaseId, self.getRelease(releaseId)

    def __len__(self):
        return len(self.metaIndex)

    def __contains__(self, releaseId):
        return releaseId in self.metaIndex

    def loadReleaseIds(self):
        fileList = os.listdir(self.rootPath)

        widgets = ["Releases: ", progressbar.Bar(marker="=", left="[", right="]"), " ", progressbar.Fraction(), " ", progressbar.Percentage() ]
        pbar = progressbar.ProgressBar(widgets=widgets, maxval=len(fileList)).start()

        for releaseId in fileList:
            if len(releaseId) == 36:
                pbar.increment()
                yield releaseId
        pbar.finish()

    def loadExtraData(self, releaseId):
        # load extra data
        self.extraIndex[releaseId] = extradata.ExtraData(releaseId)
        try:
            self.extraIndex[releaseId].load()
        except IOError as e:
            # write an empty XML for next time
            self.extraIndex[releaseId].save()

    def addExtraData(self, releaseId):
        self.extraIndex[releaseId] = extradata.ExtraData(releaseId)
        try:
            self.extraIndex[releaseId].load()
        except IOError as e:
            # write an empty XML for next time
            self.extraIndex[releaseId].addDate()
            self.extraIndex[releaseId].save()

    def load(self, releaseIds=None):
        """Load the various tables from the XML metadata on disk"""

        self.metaIndex = dict()
        self.extraIndex = dict()
        self.wordMap = dict()
        self.discIdMap = defaultdict(list)
        self.barCodeMap = defaultdict(list)
        # To map ReleaseId -> Format
        #self.formatMap = dict()

        for releaseId in self.loadReleaseIds():
            xmlPath = self._get_xml_path(releaseId)
            if (not os.path.isfile(xmlPath)):
                logging.error("Found directory, but no metadata for " + releaseId)
                continue

            metadata = self.getMetaData(releaseId)

            self.digestMetaDict(releaseId, metadata)

            self.loadExtraData(releaseId)

    def saveZip(self, zipName='catalog.zip'):
        """Exports the database as a ZIP archive"""
        import zipfile, StringIO

        with zipfile.ZipFile(zipName, 'w', zipfile.ZIP_DEFLATED) as zf:
            xml_writer = wsxml.MbXmlWriter()
            for releaseId, release in self.getReleases():
                # TODO change releaseIndex to metaIndex and store entire metadata
                # then, change references to releaseIndex to calls to getRelease(),
                # a new function that will take the release ID, and call getRelease()
                # on the appropriate metadata
                xmlPath = self._get_xml_path(releaseId)
                XmlParser = wsxml.MbXmlParser()
                with open(xmlPath, 'r') as xmlf:
                    metadata = XmlParser.parse(xmlf)

                memXmlF = StringIO.StringIO()
                xml_writer.write(memXmlF, metadata)
                memXmlF.seek(0)
                zf.writestr(xmlPath, memXmlF.read())

                # TODO write a function to produce these paths
                zf.writestr(self._get_xml_path('extra.xml'), \
                    self.extraIndex[releaseId].toString())

    def loadZip(self, zipName='catalog.zip'):
        import zipfile, StringIO

        zf = zipfile.ZipFile(zipName, 'r')
        XmlParser = wsxml.MbXmlParser()
        for fInfo in zf.infolist():
            if not fInfo.filename.startswith('release-id') or not fInfo.filename.endswith('metadata.xml'):
                continue

            rootPath, releaseId, fileName = fInfo.filename.split('/')
            memXmlF = StringIO.StringIO()
            memXmlF.write(zf.read(fInfo))
            memXmlF.seek(0)
            metadata = XmlParser.parse(memXmlF)
            self.metaIndex[releaseId] = metadata
        zf.close()

    def getReleaseWords(self, rel):
        words = []
        # TODO make combining the credits a function
        for field in [ rel['title'] ] + \
                [credit if type(credit)==type('') else credit['artist']['name'] for credit in rel['artist-credit'] ]:
            words.extend(re.findall(r"\w+", field.lower(), re.UNICODE))
        return words

    def mapWordsToRelease(self, words, releaseId):
        word_set = set(words)
        for word in word_set:
            if word in self.wordMap:
                self.wordMap[word].append(releaseId)
            else:
                self.wordMap[word] = [releaseId]

    def _search(self, query):
        query_words = query.lower().split(' ')
        matches = set()
        for word in query_words:
            if word in self.wordMap:
                # for the first word
                if word == query_words[0]:
                    # use the whole set of releases that have this word
                    matches = set(self.wordMap[word])
                else:
                    # intersect the releases that have this word with the current release set 
                    matches = matches & set(self.wordMap[word])
            else:
                # this word is not contained in any releases
                matches = set()
                break

        return matches

    def formatDiscInfo(self, releaseId):
        release = self.getRelease(releaseId)
        # TODO this is similar to other code
        return ' '.join( [
                releaseId, ':', \
                ''.join([credit if type(credit)==type('') else credit['artist']['sort-name'] for credit in release['artist-credit'] ]), '-', \
                (release['date'] if 'date' in release else ''), '-', \
                release['title'], \
                ('['+release['medium-list'][0]['format']+']' if len(release['medium-list']) else ''), \
                ] )

    def formatDiscSortKey(self, releaseId):
        release = self.getRelease(releaseId)

        try:
            return ' - '.join ( \
                    [credit if type(credit)==type('') else credit['artist']['sort-name'] for credit in release['artist-credit'] ] + \
                    #release['artist-credit-phrase'], \
                    [ release['date'] if 'date' in release else '', \
                    release['title'], \
                    ] ) 
        except IndexError as e:
            logging.warning("No releases or date for: " + releaseId + " - " + release.artist.getSortName() + "-" + release.title)

        return ' - '.join ( [
                release.artist.getSortName(), \
                "0", \
                release.title, \
                ] ) 

    def getSortedList(self, matchFmt=ReleaseFormat()):
        sortKeys = [(releaseId, self.formatDiscSortKey(releaseId)) for releaseId in self.getReleaseIds()]

        # TODO this could be sped up using a map from ReleaseId -> Format populated at loading time
        # if matching any format
        if matchFmt != ReleaseFormat():
            filteredSortKeys = []
            for sortId, sortStr in sortKeys:
                # TODO need to resolve releases with more than one format 
                # TODO make this next line a function
                if 'format' not in self.getRelease(sortId)['medium-list'][0]:
                    logging.warning('No format for ' + sortId + ", " + sortStr)
                elif 'unknown' in self.getRelease(sortId)['medium-list'][0]['format']:
                    # it's some type of unknown format message
                    logging.warning(self.getRelease(sortId)['medium-list'][0]['format'] \
                        + " format for release " + sortId + ", " + sortStr)
                elif matchFmt == ReleaseFormat(self.getRelease(sortId)['medium-list'][0]['format']):
                    filteredSortKeys.append((sortId, sortStr))
        else:
            # Skip all the fun
            filteredSortKeys = sortKeys

        self.sortedList = sorted(filteredSortKeys, key=lambda sortKey: unicode.lower(unicode(sortKey[1])))
        return self.sortedList

    def getSortNeighbors(self, releaseId, neighborHood=5, matchFormat=False):
        """
        Print release with context (neighbors) to assist in sorting and storage of releases.
        """
        # This should be broken down since it writes to the console

        if matchFormat:
            try:
                self.getSortedList(
                    ReleaseFormat(
                        self.getRelease(releaseId)['medium-list'][0]['format']))
            except IndexError as e:
                logging.warning("Sorting release " + releaseId + " with no format into a list of all releases.")
                self.getSortedList()
        else:
            self.getSortedList()

        index = self.sortedList.index((releaseId, self.formatDiscSortKey(releaseId)))
        for i in range(max(0,index-neighborHood), min(len(self.sortedList), index+neighborHood)):
            sortId, sortStr = self.sortedList[i]
            print ('\033[92m' if i == index else "") + "%4d" % i, \
                    sortId, \
                    sortStr, \
                    ("[" + self.getRelease(sortId)['medium-list'][0]['format'] + "]" if len(self.getRelease(sortId)['medium-list']) else ""), \
                    (" <<<" if i == index else "") + \
                    ('\033[0m' if i == index else "")


    def search(self, query):
        """
        Print a list releases that match words in a query
        """
        matches = self._search(query)
        for releaseId in matches:
            print self.formatDiscInfo(releaseId)

    def report(self):
        """
        Print statistics about the catalog
        """
        print
        print "%d releases" % len(self)
        print "%d words in search table" % len(self.wordMap)

    def makeHtml(self, fileName="catalog.html"):
        """
        Write HTML representing the catalog to a file
        """
        import amazonservices
        htf = open(fileName, 'w')

        htf.write("""<!DOCTYPE HTML>
<html>
<head>
<title>Music Catalog</title>
<style type="text/css">
.hasTooltip {
position:relative;
}
.hasTooltip span {
display:none;
}

.hasTooltip:hover span {
display:block;
position:absolute;
z-index:15;
background-color:black;
border-radius:5px;
color:white;
box-shadow:1px 1px 3px gray;
padding:5px;
top:1.3em;
left:0px;
white-space: nowrap;
}

</style>
</head>
<body>""")

        for releaseType in [ReleaseFormat('CD'), ReleaseFormat('Vinyl')]:
            sortedList = self.getSortedList(releaseType)
            htf.write("<h2>" + str(releaseType) + (" (%d Releases)" % len(sortedList)) + "</h2>")
            htf.write("<table>")
            htf.write("""<tr>
<!-- <th>Sort Index</th> -->
<th>Artist</th>
<th>Release Title</th>
<th>Date</th>
<th>Country</th>
<th>Label</th>
<th>Catalog #</th>
<th>Barcode</th>
<th>ASIN</th>
<th>Format</th>
<th>Date Added</th>
</tr>
""")
            # TODO there are a lot of calls to ''.encode()
            for sortIndex, (releaseId, releaseSortStr) in enumerate(sortedList):
                rel = self.getRelease(releaseId)

                #coverartUrl = amazonservices.getAsinImageUrl(rel.asin, amazonservices.AMAZON_SERVER["amazon.com"], 'S')
                # Refer to local copy instead
                imgPath = os.path.join(self.rootPath, releaseId, 'cover.jpg')
                if os.path.isfile(imgPath):
                    coverartUrl = imgPath
                else:
                    coverartUrl = None

                htf.write("<tr>")
                htf.write("<!-- <td>"+("%04d" % sortIndex)+"</td> -->")
                htf.write("<td>" + ''.join( [\
                    credit if type(credit)==type('') else \
                    "<a href=\""+self.artistUrl+credit['artist']['id']+"\">"+\
                    credit['artist']['name'].encode('ascii', 'xmlcharrefreplace')+\
                    "</a>" for credit in rel['artist-credit'] ] ) + "</td>")
                htf.write("<td><a href=\""+self.releaseUrl+rel['id']+"\"" + (" class=\"hasTooltip\"" if coverartUrl else "") + \
                    ">"+rel['title'].encode('ascii', 'xmlcharrefreplace')\
                    +(' (%s)' % rel['disambiguation'].encode('ascii', 'xmlcharrefreplace') if 'disambiguation' in rel and rel['disambiguation'] else '')\
                    +("<img width=24 height=24 src='tango/Image-x-generic.svg'><span><img width=320 height=320 src=\""+ coverartUrl +"\"></span>" \
                    if coverartUrl else "") + "</a>" + (''.join("<a href='"+path.encode('ascii', 'xmlcharrefreplace')+"'><img width=24 height=24 src='tango/Audio-x-generic.svg'></a>" for path in self.extraIndex[releaseId].digitalPaths) \
                    if self.extraIndex[releaseId].digitalPaths else "") + "</td>")
                htf.write("<td>"+(rel['date'] if 'date' in rel else '')+"</td>")
                htf.write("<td>"+(rel['country'].encode('ascii', 'xmlcharrefreplace') \
                    if 'country' in rel else '')+"</td>")
                htf.write("<td>"+', '.join([\
                    "<a href=\""+self.labelUrl+info['label']['id']+"\">"+\
                    info['label']['name'].encode('ascii', 'xmlcharrefreplace')+\
                    "</a>" if 'label' in info else '' for info in rel['label-info-list']])+"</td>")
                # TODO handle empty strings here (remove from this list before joining)
                htf.write("<td>"+', '.join([\
                    info['catalog-number'].encode('ascii', 'xmlcharrefreplace') if \
                    'catalog-number' in info else '' for info in rel['label-info-list']])+"</td>")
                htf.write("<td>"+\
                    ('' if 'barcode' not in rel else \
                    rel['barcode'] if rel['barcode'] else
                    '[none]')+"</td>")
                htf.write("<td>"+("<a href=\"" + \
                    amazonservices.getAsinProductUrl(rel['asin']) + \
                    "\">" + rel['asin'] + "</a>" if 'asin' in rel else '')+"</td>")
                htf.write("<td>"+' + '.join([medium['format'] for medium in rel['medium-list']])+"</td>")
                htf.write(\
                    "<td>"+(datetime.fromtimestamp(self.extraIndex[releaseId].addDates[0]).strftime('%Y-%m-%d') if \
                    len(self.extraIndex[releaseId].addDates) else '')+"</td>")
                htf.write("</tr>")
            htf.write("</table>")

        htf.write("<p>%d releases</p>" % len(self))

        htf.write("""</body>
</html>""")
        htf.close()

    def getReleaseMetaXml(self, releaseId):
        """Fetch release metadata XML from musicbrainz"""
        # get_release_by_id() handles throttling on its own
        mb.set_parser(mb.mb_parser_null)
        xml = mb.get_release_by_id(releaseId, includes=['artists', 'discids', 'media', 'labels', 'recordings'])
        mb.set_parser()
        return xml

    def writeXml(self, releaseId, metaData):
        global overWriteAll

        xmlPath = self._get_xml_path(releaseId)

        if not os.path.isdir(os.path.dirname(xmlPath)):
            os.mkdir(os.path.dirname(xmlPath))

        if (os.path.isfile(xmlPath) and not overWriteAll):
            print xmlPath, "already exists. Continue? [y/a/N] ",
            response = sys.stdin.readline().strip()
            if (not response or response[0] not in ['y', 'a']):
                return 1
            elif (response[0] == 'a'):
                overWriteAll = True

        logging.info("Writing metadata to '%s'", xmlPath)
        xmlf = open(xmlPath, 'w')
        xml_writer = wsxml.MbXmlWriter()
        xml_writer.write(xmlf, metaData)
        xmlf.close()

        return 0

    def fixMeta(self, discId, releaseId):
        results_meta = self.getReleaseMetaXml(releaseId)

        self.writeXml(discId, results_meta)

    def getMetaData(self, releaseId):
        """Load metadata from disk"""
        xmlPath = self._get_xml_path(releaseId)
        if (not os.path.isfile(xmlPath)):
            logging.error("No XML metadata for %s", releaseId)
            return None
        with open(xmlPath, 'r') as xmlf:
            metaxml = xmlf.read()

        try:
            metadata = mbxml.parse_message(metaxml)
        except UnicodeError as exc:
            raise ResponseError(cause=exc)
        except Exception as exc:
            if isinstance(exc, ETREE_EXCEPTIONS):
                logging.error("Got some bad XML for %s!", releaseId)
                return 
            else:
                raise

        return metadata

    def digestMetaDict(self, releaseId, metadata):
        # some dictionaries to improve performance
        # these should all be tables in an SQL DB
        try:
            rel = metadata['release']
            self.metaIndex[releaseId] = rel

            # populate DiscId map
            for medium in rel['medium-list']:
                for disc in medium['disc-list']:
                    self.discIdMap[disc['id']].append(releaseId)
                    
            # populate barcode map
            if 'barcode' in rel and rel['barcode']:
                self.barCodeMap[rel['barcode']].append(releaseId)
                    
            # for searching later
            try:
                words = self.getReleaseWords(rel)
                self.mapWordsToRelease(words, releaseId)
            except KeyError as e:
                logging.error("No artists included in XML for %s", releaseId)

        except KeyError as e:
            logging.error("Bad XML for " + releaseId + ": " + str(e))
            return
        except TypeError as e:
            logging.error(releaseId + ' ' + str(type(e)) + ": " + str(e) + ": " + str(dir(e)))
            return
        
    def digestXml(self, releaseId, meta_xml):
        self.digestMetaDict(releaseId, mbxml.parse_message(meta_xml))

    def searchDigitalPaths(self, releaseId=''):
        releaseIdList = [releaseId] if releaseId else self.getReleaseIds() 

        # TODO could use progressbar here
        for relId in releaseIdList:
            #print relId
            for path in self.prefs.musicPaths:
                #print path
                rel = self.getRelease(relId)
                for artistName in [ rel['artist-credit-phrase'], rel['artist-credit'][0]['artist']['sort-name'] ]:
                    artistPath = os.path.join(path, artistName)
                    if os.path.isdir(artistPath):
                        #print 'Found ' + artistPath
                        for titleName in [rel['title']]:
                            titlePath = os.path.join(artistPath, titleName)
                            if os.path.isdir(titlePath):
                                logging.info('Found ' + relId + ' at ' + titlePath)
                                self.extraIndex[relId].addPath(titlePath)
            self.extraIndex[relId].save()

    def refreshMetaData(self, releaseId, olderThan=0):
        """Should be renamed to "add release" or something
        get metadata XML from MusicBrainz and save to disk"""

        releaseId = getReleaseId(releaseId)
        xmlPath = self._get_xml_path(releaseId)
        if (os.path.isfile(xmlPath) and (os.path.getmtime(xmlPath) > (time.time() - olderThan))):
            logging.info("Skipping fetch of metadata for %s because it is recent", releaseId)
            return 0

        meta_xml = self.getReleaseMetaXml(releaseId)
        if not os.path.isdir(os.path.dirname(xmlPath)):
            os.mkdir(os.path.dirname(xmlPath))
        with open(xmlPath, 'w') as xmlf:
            xmlf.write(meta_xml)
        #self.writeXml(releaseId, meta_xml)
        self.digestXml(releaseId, meta_xml)

    def deleteRelease(self, releaseId):
        releaseId = getReleaseId(releaseId)
        shutil.rmtree(os.path.join(self.rootPath, releaseId))
        # also drop from memory
        #del self.releaseIndex[releaseId] # TODO necessary?
        self.load()

    def refreshAllMetaData(self, olderThan=0):
        for releaseId in self.loadReleaseIds():
            logging.info("Refreshing %s", releaseId)
            self.refreshMetaData(releaseId, olderThan)

    def checkReleases(self):
        """
        Check releases for ugliness such as no barcode, no release format, etc. 
        For now, this creates warnings in the log. 
        """
        for releaseId, release in self.getReleases():
            if release.releaseEvents:
                if not release.releaseEvents[0].barcode:
                    logging.warning("No barcode for " + releaseId + " " + \
                        self.formatDiscSortKey(releaseId) + " (" + \
                        getFormatFromUri(release.releaseEvents[0].format) + ")")
                if not release.releaseEvents[0].format:
                    logging.warning("No format for " + releaseId + " " + \
                        self.formatDiscSortKey(releaseId))

            else:
                logging.warning("No release events for", releaseId + " " + self.formatDiscSortKey(releaseId))

    def getCoverArt(self, releaseId, maxage=60*60):
        release = self.getRelease(releaseId)
        imgPath = os.path.join(self.rootPath, releaseId, 'cover.jpg')
        if os.path.isfile(imgPath) and os.path.getmtime(imgPath) > time.time() - maxage:
            logging.info("Already have cover art for " + releaseId + ", skipping")
            return

        try:
            meta = coverart.getCoverArtMeta(releaseId)
            coverart.saveCoverArt(meta, imgPath)

        except mb.ResponseError as e:
            logging.warning('No cover art for ' + releaseId + ' available from Cover Art Archive')
                
            if 'asin' in release:
                amazonservices.saveImage(release['asin'], amazonservices.AMAZON_SERVER["amazon.com"], imgPath)
            else:
                logging.warning('No ASIN for ' + releaseId)

    def refreshAllCoverArt(self, maxage=60*60*24):
        for releaseId in self.getReleaseIds():
            self.getCoverArt(releaseId, maxage=maxage)

    def checkLevenshteinDistances(self):
        import Levenshtein
        dists = []
        for leftIdx in range(len(self)):
            for rightIdx in range(leftIdx, len(self)):
                if  leftIdx == rightIdx :
                    continue
                leftId = self.getReleaseIds()[leftIdx]
                rightId = self.getReleaseIds()[rightIdx]
                dist = Levenshtein.distance(self.formatDiscSortKey(leftId),
                        self.formatDiscSortKey(rightId))

                dists.append((dist,leftId, rightId))

        return sorted(dists, key=lambda sortKey: sortKey[0])

    def getTopSimilarities(self, number=100):
        """ 
        Helps the user identify similar, possibly duplicate, releases.
        """
        lds = self.checkLevenshteinDistances()
        for i in range (number):
            logging.info(str(lds[i][0]) + "\t" + \
                self.formatDiscInfo(lds[i][1]) + " <-> " + \
                self.formatDiscInfo(lds[i][2]))

    def syncCollection(self, colId):
        """
        Synchronize the catalog with a MusicBrainz collection. 

        For now, only adds releases from the catalog to the collection
        if they do not already exist in the collection.

        In the future, should also reconcile releases in the collection
        that are not in the catalog. 
        """
        # this is a hack so that the progress will appear immediately
        import sys
        sys.stdout.write('Fetching list of releases in collection...')
        sys.stdout.flush()
        count = 0
        colRelIds = []
        while True:
            result = mb.get_releases_in_collection(colId, limit=25, offset=count)
            col = result['collection']
            relList = col['release-list']
            if len(relList) == 0:
                break
            count += len(relList)
            sys.stdout.write('.')
            sys.stdout.flush()
            for rel in relList:
                colRelIds.append(rel['id'])
                
        #colRelList = mb.get_releases_in_collection(colId)
        print 'OK'
        print 'Found %d / %d releases.' % (len(colRelIds), len(self))

        relIdsToAdd = list(set(self.getReleaseIds()) - set(colRelIds))

        print 'Going to add %d releases to collection...' % len(relIdsToAdd)
        for relIdChunk in chunks(relIdsToAdd, 100):
            mb.add_releases_to_collection(colId, relIdChunk)
        print 'DONE'

