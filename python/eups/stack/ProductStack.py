import re, os, cPickle, sys
from eups import Product
from ProductFamily import ProductFamily
from eups.exceptions import ProductNotFound, UnderSpecifiedProduct
from eups.db import Database
import eups.lock

# Issues:
#  o  restoring from cache (when and how) 

# the version name for the persistence format used by this implementation.
# It is intended to match the version of EUPS when this format was introduced
persistVersionName = "1.2.0"

# the prefix to a tag name that labels it as a user tag.  Anything else is 
# considered a global tag.
userPrefix = "user."     

dotre = re.compile(r'\.')
who = os.getlogin()

class ProductStack(object):
    """
    a lookup for products installed into a software "stack" managed by 
    EUPS via a single ups_db database.  

    This class is can persist its data to disk on every update when 
    instantiated with saving turned on (save contructor parameter).
    The instantiater is responsible for ensuring that the current user 
    has permission to write to caching directory (given via the constructor
    or defaults to the ups_db directory.  

    It also supports a notion of global versus user tags.  Tags that should 
    be considered as user-specific should start with the prefix "user.".  
    This will affect where tag information is persisted.  A user tag is
    persisted to a special user directory (set via the userTagPersistDir 
    constructor parameter).  A global tag is persisted along with the rest of 
    the product data.  

    Note that this class does not keep track of what are considered allowed 
    tag names.  The user of this class should manage this.  
    """
    # static variable: version of Product stack cache, set to the EUPS 
    # version when the format was introduced
    persistVersion = persistVersionName

    # static variable: name of file extension to use to persist data
    persistFileExt = "pickleDB%s" % dotre.sub('_', persistVersionName)

    # static variable: regexp for cache file names
    persistFileRe = re.compile(r'^(\w\S*)\.%s$' % persistFileExt)

    # static variable: name of file extension to use to persist data
    userTagFileExt = "pickleTag%s" % dotre.sub('_', persistVersionName)

    def __init__(self, dbpath, userTagPersistDir=None, prodPersistDir=None, 
                 autosave=True):
        """
        create the stack with a given database
        @param dbpath             the path to the ups_db directory
        @param userTagPersistDir  the directory to persist user tag data to
        @param prodPersistDir     the directory to persist to.  If None,
                                     the dbpath value will be used as the 
                                     directory.
        @param autosave           if true (default), all updates will be 
                                     saved to disk.
        """
        # the path to the ups_db directory
        self.dbpath = dbpath
        if not self.dbpath:
            raise RuntimeError("Empty/None given as EUPS database path: " + 
                               str(dbpath))
        if not os.path.exists(self.dbpath):
            raise IOError(dbpath + ": EUPS database directory not found")

        # a hierarchical dictionary for looking up products.  The dimensions 
        # of the hierarchy (from left to right, general to specific) are:
        #   * flavor
        #   * product name
        # The values at the bottom of the hierarchy are ProductFamily 
        # instances
        self.lookup = {}

        # if true, the product data automatically will be cached on each update
        self.autosave = autosave

        # a list of flavors for which product data has changed since the 
        # last time it was cached to disk.  If empty, then no changes are 
        # pending
        self.updated = []

        # a lookup of modification times for the underlying cachefiles 
        # by cachefile name when data was loaded in from this cache.  
        # If a target cache file has been updated since then, we should
        # not save any new changes to it.  
        self.modtimes = {}

        # the directory to persist this data to when save is called.  If None,
        # a default path will be dbpath.
        self.persistDir = prodPersistDir
        if self.persistDir is not None and self.autosave and \
           not os.path.exists(self.persistDir):
              raise IOError("Directory not found: " + self.persistDir)

        # user tag assignments stored a hierarchical dictionary.  The 
        # dimensions of the hierarchy (from left to right, general to 
        # specific) are:
        #   * flavor
        #   * tag
        #   * product name
        # the values at the bottom are version names
        self.usertags = {}

        # the directory to persist user tag data to when assigning tags
        # If None, user tags will not be persisted
        self.userTagDir = userTagPersistDir
        if self.userTagDir is not None and self.autosave and \
           not os.path.exists(self.userTagDir):
            raise IOError("Directory not found: " + self.userTagDir)


    def getDbPath(self):
        """
        return the path to the ups_db directory containing the stack database

        @return string : the path to the ups_db directory
        """
        return self.dbpath

    def getFlavors(self):
        """
        return the platform flavors supported by this stack
        """
        return self.lookup.keys()

    def getTags(self, flavor=None):
        """
        return all assigned tags assigned on this stack
        """
        if flavor is None:
            prods = _lol2l(self.lookup.values(), lambda y: y.values())
        else:
            prods = self.lookup[flavor].values()

        return _uniquify(_lol2l(map(lambda z: z.getTags(), prods)))

    def getProductNames(self, flavor=None):
        """
        return the names of all declared products
        """
        if flavor is None:
            return _uniquify(_lol2l(map(lambda z: z.keys(), 
                                        self.lookup.values())))
        else:
            return self.lookup[flavor].keys()

    def getVersions(self, productName, flavor=None):
        """
        return the versions declared for all declared products
        @param productName   the name of the product of interest
        @param flavor        the flavor to search; if None, return for all
                                flavors
        """
        try:
          if flavor is None:
            return _uniquify(_lol2l(map(lambda z: z[productName].getVersions(),
                                        self.lookup.values())))
          else:
            return self.lookup[flavor][productName].getVersions()
        except KeyError:
          return []

    def hasProduct(self, name, flavor=None, version=None):
        """
        return true if a desired product is registered.

        @param name :    the product name
        @param flavor :  the desired flavor.  If None, any existing 
                             flavor will return true
        @param version : the desired version name.  If None, any existing 
                             version will return true.
        @return bool :
        """
        if flavor is None:
            for flavor in self.lookup.keys():
                if self.hasProduct(name, flavor, version):
                    return True
            return False

        try:
            pf = self.lookup[flavor][name]
            if version is None:  return True
            return pf.hasVersion(version)
        except KeyError:
            return False

    def getProduct(self, name, version, flavor):
        """
        lookup and return a Product description given the product name, 
        flavor, and version.  All parameters must be provided.

        @param name :     the product name
        @param version :  the desired version name
        @param flavor :   the desired platform flavor
        @return Product : the requested product 
        """
        try:
            out = self.lookup[flavor][name].getProduct(version)
            out.flavor = flavor
            out.db = self.dbpath
            out._prodStack = self
            return out
        except KeyError:
            raise ProductNotFound(name, version, flavor)

    # @staticmethod   # requires python 2.4
    def persistFilename(flavor):
        return "%s.%s" % (flavor, ProductStack.persistFileExt)
    persistFilename = staticmethod(persistFilename)  # works since python 2.2

    def save(self, flavors=None, dir=None):
        """
        persist the product information to disk.  If a cache file for a 
        flavor is newere than when we loaded from it last, that flavor 
        will not be saved, and a RuntimeError will be raised.  Other flavors,
        will be saved, though.
        @param flavors  the flavors to persist.  This can be a single string 
                           (for a single flavor) or a list of flavors.  If 
                           None, save all flavors that appear to need updating
        @param file     the file to save it to.  
        """
        if flavors is None:
            if not self.updated: return 
            return self.save(self.updated)
        if not isinstance(flavors, list):
            flavors = [flavors]

        outofsync = []
        for flavor in flavors:
            file = self._persistPath(flavor, dir)
            if self.modtimes.has_key(file) and \
               self.modtimes[file] < os.stat(file).st_mtime:
                # file was updated since we loaded from it last!
                outofsync.append(file)
                continue

            self.persist(flavor, file)
            if dir is None:
                self.updated = filter(lambda x: x != flavor, self.updated)

        if len(outofsync) > 0:
            raise RuntimeError("In-memory cache appears out of sync with "+
                               "cache files: " + str(outofsync))
            

    def _persistDir(self, dir=None):
        if not dir:
            dir = self.persistDir
            if not dir:
                dir = self.dbpath
        return dir

    def _persistPath(self, flavor, dir=None):
        return os.path.join(self._persistDir(dir), self.persistFilename(flavor))

    def persist(self, flavor, file=None):
        """
        persist the product information for a particular flavor to a file
        @param flavor   the flavor to persist.
        @param file     the name of the file to persist to.  If it already 
                          exists, it will be overwritten.  If value is None,
                          a location will be be used.
        """
        if file is None:
            dir = self.persistDir
            if not dir:
                dir = self.dbpath
            file = os.path.join(dir, persistFilename(flavor))

        if not self.lookup.has_key(flavor):
            self.lookup[flavor] = {}
        flavorData = self.lookup[flavor]

        self._lock(file)
        fd = open(file, "w")
        cPickle.dump(flavorData, fd)
        fd.close()
        self.modtimes[file] = os.stat(file).st_mtime
        self._unlock(file)

    def export(self):
        """
        return a hierarchical dictionary of all the Products in the stack, 
        suitable for persisting.  The dimensions of the hierarchy (from 
        left to right, general to specific) are:
           * flavor
           * product name
           * version name
        The values at the bottom of the hierarchy are Product instances

        @return dictionary : the exported products
        """
        out = {}
        for flavor in self.lookup.keys():
            out[flavor] = {}
            for product in self.lookup[flavor].keys():
                out[flavor][product] = \
                    self.lookup[flavor][product].export(self.db, flavor)
        return out

    def addFlavor(self, flavor): 
        """
        register a flavor without products.  

        This makes a flavor recognized even though no products have been 
        registered for this flavor.  It is typical for an Eups instance to 
        want to load product information for more than one flavor as back-up
        to the native flavor.  It is helpful, then to provide an empty lookup
        in this ProductStack, so that the Eups instance doesn't need 
        continually filter its desired flavors against the ones we actually 
        have products for.  In particular, it allows info an empty flavor to be 
        cached just like normal flavor.  
        """
        if not self.lookup.has_key(flavor):
            self.lookup[flavor] = {}

    def addProduct(self, product):
        """
        register a product with a particular flavor

        @param product : register a product.  If this particular product 
                            is already registered, its information will be 
                            over-written
        """
        if not isinstance(product, Product):
            raise TypeError("non-Product passed to addProduct()")
        if not product.name or not product.version or not product.flavor:
            raise UnderSpecifiedProduct(
                msg="Product not fully specified: %s %s %s" 
                    % (str(product.name), str(product.version),
                       str(product.flavor))
            )

        flavor = product.flavor
        if not self.lookup.has_key(flavor):
            self.lookup[flavor] = {}
        if not self.lookup[flavor].has_key(product.name):
            self.lookup[flavor][product.name] = ProductFamily(product.name)
        self.lookup[flavor][product.name].addVersion(product.version,
                                                     product.dir,
                                                     product.tablefile,
                                                     product._table)
        for tag in product.tags:
            self.lookup[flavor][product.name].assignTag(tag, product.version)

        self._flavorsUpdated(flavor)
        if self.autosave: self.save(flavor)

    def _flavorsUpdated(self, flavors=None):
        # this function is called whenever the stack is updated to add
        # the updated flavors to self.updated.  The value of self.updated,
        # therefore, indicates which flavors need to updated to disk.
        if flavors is None:
            self.updated = self.getFlavors()
        elif isinstance(flavors, list):
            self.updated.extend(filter(lambda x: x not in self.updated,flavors))
        elif flavors not in self.updated:
            self.updated.append(flavors)

    def saveNeeded(self, flavors=None):
        """
        return true if there are unsaved updates to this product stack.  
        @param favors   restrict answer to the given flavors.  This parameter
                          can be given either as a single string (for a 
                          single flavor) or as a list of flavor names.  If 
                          None, true will be returned if any flavor has been
                          updated.
        """
        if flavors is None:
            return len(self.updated) > 0

        if not isinstance(flavors, list):
            flavors = [flavors]

        for flavor in flavors:
            if flavor in self.updated:
                return True
        return False
          


    def import_(self, products):
        """
        import a set of products in a hierarchical dictionary.  
        @param products : the hierarchical dictionary containing the 
                            products.  The dimensions of the hierarchy 
                            (from left to right, general to specific) are:
                              * flavor
                              * product name
                              * version name
                            The values at the bottom of the hierarchy are 
                            Product instances.
        """
        updated = False
        for flavor in products.keys():
            if not self.lookup.has_key(flavor):
                self.lookup[flavor] = {}
            for product in products[flavor].keys():
                if not self.lookup[flavor].has_key(product):
                    self.lookup[flavor][product] = ProductFamily(product)
                self.lookup[flavor][product].import_(products[flavor][product])
                updated = True
                self._flavorsUpdated(flavor)

        if self.autosave and updated: self.save()

    def removeProduct(self, name, flavor, version):
        """
        unregister a product, return false if the product is not found.

        @param name :    the name of the product
        @param flavor :  the platform flavor of the product
        @param version : the version name
        @return bool :
        """
        try:
            updated = self.lookup[flavor][name].removeVersion(version)
            if updated:
                if len(self.lookup[flavor][name].getVersions()) == 0:
                    del self.lookup[flavor][name]
                self._flavorsUpdated(flavor)
                if self.autosave: self.save(flavor)
        except KeyError:
            return False
        return updated

    def getTaggedProduct(self, name, flavor, tag):
        """
        return a version of a Product with the given tag assigned to it 
        or None if no version of the product is so tagged.

        @param name :   the product name
        @param flavor : the product flavor
        @param tag :    the desired tag name
        @return Product :
        """
        try:
            return self.lookup[flavor][name].getTaggedProduct(tag, 
                                                              self.dbpath, 
                                                              flavor)
        except KeyError:
            return None
        

    def assignTag(self, tag, product, version, flavors=None):
        """
        assign a tag to a given version of a product.  If tag does not 
        start with "user." (indicating a global tag) but 
        the user does not have permission to write into the stack database, 
        an exception is raised.

        @param tag :     the tag name to be assigned.  If this name starts 
                           with "user.", it will be considered a user prefix 
                           and thus the assignment will be cached to a 
                           the user-specific location.  
        @param product : the name of the product the tag is being assigned to 
        @param version : the version to assign tag to
        @param flavor :  the platform flavor name or names.  The value can 
                           be a single string or a list.  If None, assign the 
                           tag for specified version of all flavors.
        @throws ProductNotFound   if the specified product is not found
        """
        if flavors is None:
            return self.assignTag(tag, product, version, self.lookup.keys())

        notfound = True
        if not isinstance(flavors, list):
            flavors = [flavors]
        for flavor in flavors:
            try:
                self.lookup[flavor][product].assignTag(tag, version)
                if tag.startswith(userPrefix):
                    self._setUserTag(flavor, tag, product, version)
                notfound = False
            except KeyError:
                pass
        if notfound:
            raise ProductNotFound(product, version, flavors, self.dbpath)

        self._flavorsUpdated(flavors)
        if self.autosave: 
            if tag.startswith(userPrefix):
                self._saveTag(tag, flavors)
            else:
                self.save(flavors)

    def _setUserTag(self, flavor, tag, product, version):
        if not self.usertags.has_key(flavor):
            self.usertags[flavor] = {}
        if not self.usertags[flavor].has_key(tag):
            self.usertags[flavor][tag] = {}
        self.usertags[flavor][tag][product] = version

    def _unsetUserTag(self, flavor, tag, product):
        try:
            del self.usertags[flavor][tag][product]
        except KeyError:
            pass

    def _saveTag(self, tag, flavors):
        if self.userTagDir:
            for flavor in flavors:
                if self.usertags.has_key(flavor) and \
                   self.usertags[flavor].has_key(tag):
                    self._lock(file)
                    file = "%s_%s.pickleTag%s" % (flavor, tag, userTagFileExt)
                    file = os.path.join(self.userTagDir, file)
                    fd = open(file, "w")
                    cPickle.dump(self.usertags[flavor][tag], fd)
                    fd.close()
                    self._unlock(file)



    def unassignTag(self, tag, product, flavors=None):
        """
        remove the given tag from a product

        @param tag :     the name of the tag to remove
        @param product : the name of product to remove tag from.
        @param flavors : the flavors to unassign the tag for.  If None, 
                           the tag will be unassigned for all flavors
        @return bool :   return false if the tag was not found assigned to 
                           any product or product was not found.
        """
        if flavors is None:
            return self.unassignTag(tag, product, self.getFlavors())

        if not isinstance(flavors, list):
            flavors = [flavors]

        updated = False
        for flavor in flavors:
            try:
                if (self.lookup[flavor][product].unassignTag(tag)):
                    updated = True
                    self._flavorsUpdated(flavor)
                    if tag.startswith(userPrefix):
                        self._unsetUserTag(flavor, tag, product)
            except KeyError:
                pass

        if updated and self.autosave: 
            if tag.startswith(userPrefix):
                self._saveTag(tag, flavors)
            else:
                self.save(flavors)
        return updated

    def loadTableFor(self, productName, version, flavor, table=None):
        """
        cache the parsed contents of the table file for a given product.
        If table is not None,
        it will be taken as the Table instance representing the already 
        parsed contents; otherwise, the table will be loaded from the 
        table file path.  

        @param productName  the name of the desired product
        @param version      the version of the product to load
        @param flavor       the product's flavor
        @param table        an instance of Table to accept as the loaded
                               contents
        """
        try:
            self.lookup[flavor][name].loadTableFor(version, table)
            self._flavorsUpdated(flavor)
        except KeyError:
            raise ProductNotFound(name, version, flavor)


    def _lockfilepath(self, file):
        return file + ".lock"

    def _lock(self, file):
        eups.lock.lock(self._lockfilepath(file), who)

    def _unlock(self, file):
        eups.lock.unlock(self._lockfilepath(file), who)


    def cacheIsUpToDate(self, flavor):
        """
        return True if there is a cache file on disk with product information
        for a given flavor which is newer than the information in the 
        product database.  False is returned if the file does not exist
        or otherwise appears out-of-date.
        """
        cache = self._persistPath(flavor)
        if not os.path.exists(cache):
            return False

        # get the modification time of the cache file
        cache_mtime = os.stat(cache).st_mtime

        # this is slightly inaccurate: data for any flavor in the database
        # is newer than this time, this isNewerThan() returns True
        return not Database(self.dbpath).isNewerThan(cache_mtime)

    def clearCache(self, flavors=None, cachedir=None):
        """
        remove the cache file containing the persisted product information for 
        the given flavors.  
        @param flavors    the platform flavors to clear caches for.  This value
                            can be a single flavor name (as a string) or a list 
                            of flavors.
        """
        if not flavors:
            return self.clearCache(self.getFlavors(), cachedir)
        if not isinstance(flavors, list):
            flavors = [flavors]

        for flavor in flavors:
            file = self._persistPath(flavor, cachedir)
            if os.path.exists(file):
                os.remove(file)

    def reload(self, flavors=None, userTagPersistDir=None, prodPersistDir=None):
        """
        throw away all information on products and replace it with the data
        saved in the cache files.

        @param flavors            if not None, restrict reloading to the given
                                    flavors; other flavor data will remain
                                    unchanged.
        @param userTagPersistDir  the directory to find cached user tag data.
                                    If None, the directory set at construction
                                    time will be used.  (This will not change
                                    where tag data is subsequently saved; the
                                    construction-time set directory will still
                                    be used.)  If the directory does not exist,
                                    user tags will not be read in.
        @param prodPersistDir     the directory to find cached product data.
                                    If None, the directory set at construction
                                    time will be used.  (This will not change
                                    where tag data is subsequently saved; the
                                    construction-time set directory will still
                                    be used.)  If this tag does not exist, 
                                    a RuntimeError is raised.
        """
        if userTagPersistDir is None:
            userTagPersistDir = self.userTagDir
        if not userTagPersistDir or not os.path.isdir(userTagPersistDir):
            userTagPersistDir = None

        if prodPersistDir is None:
            prodPersistDir = self._persistDir()
        if not prodPersistDir:
            raise RuntimeError("ProductStack.reload(): a cache directory "+
                               "is needed: " + str(prodPersistDir))
        if not os.path.isdir(prodPersistDir):
            raise RuntimeError(prodPersistDir + ": not an existing directory")

        if flavors is None:
            flavors = self.findCachedFlavors(prodPersistDir)
        if not isinstance(flavors, list):
            flavors = [flavors]

        for flavor in flavors:
            file = self._persistPath(flavor,prodPersistDir)
            self._lock(file)
            self.modtimes[file] = os.stat(file).st_mtime
            fd = open(file)
            lookup = cPickle.load(fd)
            fd.close()
            self._unlock(file)

            self.lookup[flavor] = lookup

            # update with user tags
            


    # @staticmethod   # requires python 2.4
    def findCachedFlavors(dir):

        # read comments from bottom to top
        return map(lambda a: a.group(1),  # extra flavor name
                   filter(lambda b: b,    # grab only cache files
                          # match file against cache file pattern
                          map(lambda c: ProductStack.persistFileRe.match(c),
                              # list contents of directory
                              os.listdir(dir))))

    findCachedFlavors = staticmethod(findCachedFlavors) # works since python2.2

    def refreshFromDatabase(self):
        """
        load product information directly from the database files on disk,
        overwriting any previous information.
        """
        db = Database(self.dbpath)

        # forget!
        self.lookup = {}

        for prodname in db.findProductNames():
            for product in db.findProducts(prodname):
                self.addProduct(product)

    # @staticmethod   # requires python 2.4
    def fromDatabase(dbpath, userTagPersistDir=None, prodPersistDir=None, 
                     autosave=True):
        """
        return a ProductStack that has all products loaded in from an EUPS
        database
        @param dbpath   the full path to the database directory ("ups_db")
        @param userTagPersistDir  the directory to persist user tag data to
        @param prodPersistDir     the directory to persist to.  If None,
                                     the dbpath value will be used as the 
                                     directory.
        @param autosave           if true (default), all updates will be 
                                     saved to disk.
        """
        out = ProductStack(dbpath,userTagPersistDir,prodPersistDir,autosave)
        out.refreshFromDatabase()
        return out
    fromDatabase = staticmethod(fromDatabase)    # works since python2.2

    # @staticmethod   # requires python 2.4
    def fromCache(dbpath, flavors, userTagPersistDir=None, prodPersistDir=None, 
                  updateCache=True, autosave=True, verbose=False):
        """
        return a ProductStack that has all products loaded in from the available 
        caches.  If they are out of date (or non-existent), this will refresh
        from the database.
        @param dbpath   the full path to the database directory ("ups_db")
        @param flavors            the desired flavors
        @param userTagPersistDir  the directory to persist user tag data to
        @param prodPersistDir     the directory to persist to.  If None,
                                     the dbpath value will be used as the 
                                     directory.
        @param updateCache        if true (default), update the caches if any 
                                     appear out of date
        @param autosave           if true (default), all updates will be 
                                     saved to disk.
        """
        if not flavors:
            raise RuntimeError("ProductStack.fromCache(): at least one flavor needed as input" +
                               str(flavors));
        if not isinstance(flavors, list):
            flavors = [flavors]

        out = ProductStack(dbpath,userTagPersistDir,prodPersistDir,autosave)

        cacheOkay = True
        for flav in flavors:
            if not out.cacheIsUpToDate(flav):
                cacheOkay = False
                if verbose:
                  print >> sys.stderr, \
                   "Regenerating missing or out-of-date cache cache for %s in\n   %s" % (flav, dbpath)
                break
        if cacheOkay:
            out.reload(flavors)
        else:
            out.refreshFromDatabase()
            out._flavorsUpdated(flavors)
            if updateCache:  out.save()

        return out

    fromCache = staticmethod(fromCache)    # works since python2.2

def _uniquify(lis):
    for i in xrange(len(lis)):
        item = lis.pop(0)
        if item not in lis:  lis.append(item)
    lis.sort()
    return lis

def _lol2l(lol, tolist=None):
    # convert a list-of-lists to a single list
    # @param lol     the list of lists
    # @param tolist  a function to apply outer list item to transform it
    #                   into a list
    out =[]
    for l in lol:
        if tolist:  l = tolist(l)
        out.extend(l)
    return out

