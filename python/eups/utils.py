"""
Utility functions used across EUPS classes.
"""
import time

def _svnRevision(file=None, lastChanged=False):
    """Return file's Revision as a string; if file is None return
    a tuple (oldestRevision, youngestRevision, flags) as reported
    by svnversion; e.g. (4123, 4168, ("M", "S")) (oldestRevision
    and youngestRevision may be equal)
    """

    if file:
        info = getInfo(file)

        if lastChanged:
            return info["Last Changed Rev"]
        else:
            return info["Revision"]

    if lastChanged:
        raise RuntimeError, "lastChanged makes no sense if file is None"

    res = os.popen("svnversion . 2>&1").readline()

    if res == "exported\n":
        raise RuntimeError, "No svn revision information is available"

    mat = re.search(r"^(?P<oldest>\d+)(:(?P<youngest>\d+))?(?P<flags>[MS]*)", res)
    if mat:
        matches = mat.groupdict()
        if not matches["youngest"]:
            matches["youngest"] = matches["oldest"]
        return matches["oldest"], matches["youngest"], tuple(matches["flags"])

    raise RuntimeError, ("svnversion returned unexpected result \"%s\"" % res[:-1])

def version():
    """Set a version ID from an svn ID string (dollar HeadURL dollar)"""

    versionString = r"$HeadURL: svn+ssh://svn.lsstcorp.org/eups/trunk/eups.py $"

    version = "unknown"

    if re.search(r"^[$]HeadURL:\s+", versionString):
        # SVN.  Guess the tagname from the last part of the directory
        try:
            version = re.search(r"/([^/]+)$", os.path.split(versionString)[0]).group(1)

            if version == "trunk":
                version = "svn"
                try:                    # try to add the svn revision to the version
                    (oldest, youngest, flags) = _svnRevision()
                    version += youngest
                except IOError:
                    pass
        except RuntimeError:
            pass

    return version

def debug(*args, **kwargs):
    """
    Print args to stderr; useful while debugging as we source the stdout 
    when setting up.  Specify eol=False to suppress newline"""

    print >> sys.stderr, "Debug:", # make sure that this routine is only used for debugging
    
    for a in args:
        print >> sys.stderr, a,

    if kwargs.get("eol", True):
        print >> sys.stderr

def ctimeTZ(t=None):
    """Return a string-formatted timestampe with time zone"""

    if not t:
        t = time.localtime()

    return time.strftime("%Y/%m/%d %H:%M:%S %Z", t)

def isRealFilename(filename):
    """
    Return True iff "filename" is a real filename, not a placeholder.  
    It need not exist.  The following names are considered placeholders:
    ["none", "???"].
    """

    if filename is None:
        return False
    elif filename in ("none", "???"):
        return False
    else:
        return True
    
def isDbWritable(dbpath):
    """
    return true if the database is updatable.  A non-existent
    directory is considered not writable.  If the path is not a
    directory, an exception is raised.  

    The database must be writable to:
      o  declare new products
      o  set or update global tags
      o  update the product cache
    """
    return os.access(dbpath.access(os.F_OK|os.R_OK|os.W_OK))

def findWritableDb(pathdirs):
    """return the first directory in the eups path that the user can install 
    stuff into
    """
    if isinstance(pathdirs, str):
        pathdirs = pathdirs.split(':')
    if not isinstance(pathdirs, list):
        raise TypeError("findWritableDb(): arg is not list or string: " + 
                        pathdirs)
    for path in pathdirs:
        if isDbWritable(path):
            return path

    return None

def version_cmp(v1, v2, suffix=False):
    """Here's the internal routine that _version_cmp uses.
    It's split out so that we can pass it to the callback
    """

    def split_version(version):
        # Split a version string of the form VVV([m-]EEE)?([p+]FFF)?
        if not version:
            return "", "", ""

        if len(version.split("-")) > 2: # a version string such as rel-0-8-2 with more than one hyphen
            return version, "", ""

        mat = re.search(r"^([^-+]+)((-)([^-+]+))?((\+)([^-+]+))?", version)
        vvv, eee, fff = mat.group(1), mat.group(4), mat.group(7)

        if not eee and not fff:             # maybe they used VVVm# or VVVp#?
            mat = re.search(r"(m(\d+)|p(\d+))$", version)
            if mat:
                suffix, eee, fff = mat.group(1), mat.group(2), mat.group(3)
                vvv = re.sub(r"%s$" % suffix, "", version)

        return vvv, eee, fff

    prim1, sec1, ter1 = split_version(v1)
    prim2, sec2, ter2 = split_version(v2)

    if prim1 == prim2:
        if sec1 or sec2 or ter1 or ter2:
            if sec1 or sec2:
                if (sec1 and sec2):
                    ret = version_cmp(sec1, sec2, True)
                else:
                    if sec1:
                        return -1
                    else:
                        return 1

                if ret == 0:
                    return version_cmp(ter1, ter2, True)
                else:
                    return ret

            return version_cmp(ter1, ter2, True)
        else:
            return 0

    c1 = re.split(r"[._]", prim1)
    c2 = re.split(r"[._]", prim2)
    #
    # Check that leading non-numerical parts agree
    #
    if not suffix:
        prefix1, prefix2 = "", ""
        mat = re.search(r"^([^0-9]+)", c1[0])
        if mat:
            prefix1 = mat.group(1)

        mat = re.search(r"^([^0-9]+)", c2[0])
        if mat:
            prefix2 = mat.group(1)

        if len(prefix1) > len(prefix2): # take shorter prefix
            prefix = prefix2
            if not re.search(r"^%s" % prefix, c1[0]):
                return +1
        else:
            prefix = prefix1
            if not re.search(r"^%s" % prefix1, c2[0]):
                return -1

        c1[0] = re.sub(r"^%s" % prefix, "", c1[0])
        c2[0] = re.sub(r"^%s" % prefix, "", c2[0])

    n1 = len(c1); n2 = len(c2)
    if n1 < n2:
        n = n1
    else:
        n = n2

    for i in range(n):
        try:                        # try to compare as integers, having stripped a common prefix
            _c2i = None             # used in test for a successfully removing a common prefix

            mat = re.search(r"^([^\d]+)\d+$", c1[i])
            if mat:
                prefixi = mat.group(1)
                if re.search(r"^%s\d+$" % prefixi, c2[i]):
                    _c1i = int(c1[i][len(prefixi):])
                    _c2i = int(c2[i][len(prefixi):])

            if _c2i is None:
                _c1i = int(c1[i])
                _c2i = int(c2[i])

            c1[i] = _c1i
            c2[i] = _c2i
        except ValueError:
            pass

        different = cmp(c1[i], c2[i])
        if different:
            return different

    # So far, the two versions are identical.  The longer version should sort later
    return cmp(n1, n2)

def determineFlavor():
    """Return the current flavor"""
    
    if os.environ.has_key("EUPS_FLAVOR"):
        return os.environ["EUPS_FLAVOR"]

    uname = os.uname()[0]
    mach =  os.uname()[4]

    if uname == "Linux":
       if re.search(r"_64$", mach):
           flav = "Linux64"
       else:
           flav = "Linux"
    elif uname == "Darwin":
       if re.search(r"i386$", mach):
           flav = "DarwinX86"
       else:
           flav = "Darwin"
    else:
        raise RuntimeError, ("Unknown flavor: (%s, %s)" % (uname, mach))

    return flav    
    
def guessProduct(dir, productName=None):
    """Guess a product name given a directory containing table files.  If you provide productName,
    it'll be chosen if present; otherwise if dir doesn't contain exactly one product we'll raise RuntimeError"""

    if not os.path.isdir(dir):
        # They may have specified XXX but dir == XXX/ups
        root, leaf = os.path.split(dir)
        if leaf == "ups" and not os.path.isdir(root):
            dir = root
            
        raise RuntimeError, ("%s isn't a directory" % dir)
            
    productNames = map(lambda t: re.sub(r".*/([^/]+)\.table$", r"\1", t), glob.glob(os.path.join(dir, "*.table")))

    if not productNames:
        raise RuntimeError, ("I can't find any table files in %s" % dir)

    if productName:
        if productName in productNames:
            return productName
        else:
            raise RuntimeError, ("You chose product %s, but I can't find its table file in %s" % (productName, dir))
    elif len(productNames) == 1:
        return productNames[0]
    else:
        raise RuntimeError, \
              ("I can't guess which product you want; directory %s contains: %s" % (dir, " ".join(productNames)))

class Flavor(object):
    """A class to handle flavors"""

    def __init__(self):
        try:
            Flavor._fallbackFlavors
        except AttributeError:
            Flavor._fallbackFlavors = {}

            self.setFallbackFlavors(None)
        
    def setFallbackFlavors(self, flavor=None, fallbackList=["NULL", "Generic"]):
        """
        Set a list of alternative flavors to be used if a product can't 
        be found with the given flavor
        """
        Flavor._fallbackFlavors[flavor] = fallbackList

    def getFallbackFlavors(self, flavor=None, includeMe=False):
        """
        Return the list of alternative flavors to use if the specified 
        flavor is unavailable.  The alternatives to None are always available

        If includeMe is true, include flavor as the first element 
        of the returned list of flavors
        """
        try:
            fallbacks = Flavor._fallbackFlavors[flavor]
        except KeyError:
            fallbacks = Flavor._fallbackFlavors[None]

        if flavor and includeMe:
            fallbacks = [flavor] + fallbacks

        return fallbacks

# Note: setFallbackFlavors is made available to our beloved users via 
# eups/__init__.py
# 
# setFallbackFlavors = Flavor().setFallbackFlavors 

class Quiet(object):
    """A class whose members, while they exist, make Eups quieter"""

    def __init__(self, Eups):
        self.Eups = Eups
        self.Eups.quiet += 1

    def __del__(self):
        self.Eups.quiet -= 1
