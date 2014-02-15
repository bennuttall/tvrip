# vim: set noet sw=4 ts=4:

# External utilities
PYTHON=python
PYFLAGS=
DEST_DIR=/

# Horrid hack to ensure setuptools is installed in our python environment. This
# is necessary with Python 3.3's venvs which don't install it by default.
ifeq ($(shell python -c "import setuptools" 2>&1),)
SETUPTOOLS:=
else
SETUPTOOLS:=$(shell wget https://bitbucket.org/pypa/setuptools/raw/bootstrap/ez_setup.py -O - | $(PYTHON))
endif

# Calculate the base names of the distribution, the location of all source,
# documentation and executable script files
NAME:=$(shell $(PYTHON) $(PYFLAGS) setup.py --name)
VER:=$(shell $(PYTHON) $(PYFLAGS) setup.py --version)
PYVER:=$(shell $(PYTHON) $(PYFLAGS) -c "import sys; print 'py%d.%d' % sys.version_info[:2]")
PY_SOURCES:=$(shell \
	$(PYTHON) $(PYFLAGS) setup.py egg_info >/dev/null 2>&1 && \
	cat $(NAME).egg-info/SOURCES.txt)
DOC_SOURCES:=$(wildcard docs/*.rst)

# Calculate the name of all outputs
DIST_EGG=dist/$(NAME)-$(VER)-$(PYVER).egg
DIST_EXE=dist/$(NAME)-$(VER).win32.exe
DIST_RPM=dist/$(NAME)-$(VER)-1.src.rpm
DIST_TAR=dist/$(NAME)-$(VER).tar.gz
DIST_DEB=dist/$(NAME)_$(VER)-1~ppa1_all.deb
MAN_DIR=build/sphinx/man
MAN_PAGES=$(MAN_DIR)/tvrip.1

# Default target
all:
	@echo "make install - Install on local system"
	@echo "make develop - Install symlinks for development"
	@echo "make test - Run tests"
	@echo "make doc - Generate HTML and PDF documentation"
	@echo "make source - Create source package"
	@echo "make egg - Generate a PyPI egg package"
	@echo "make rpm - Generate an RedHat package"
	@echo "make deb - Generate a Debian package"
	@echo "make dist - Generate all packages"
	@echo "make clean - Get rid of all generated files"
	@echo "make release - Create, tag, and upload a new release"
	@echo "make upload - Upload the new release to repositories"

install:
	$(PYTHON) $(PYFLAGS) setup.py install --root $(DEST_DIR)

doc: $(DOC_SOURCES)
	$(PYTHON) $(PYFLAGS) setup.py build_sphinx -b html
	$(PYTHON) $(PYFLAGS) setup.py build_sphinx -b man
	$(PYTHON) $(PYFLAGS) setup.py build_sphinx -b latex
	$(MAKE) -C build/sphinx/latex all-pdf

source: $(DIST_TAR) $(DIST_ZIP)

egg: $(DIST_EGG)

rpm: $(DIST_RPM)

deb: $(DIST_DEB)

dist: $(DIST_EGG) $(DIST_RPM) $(DIST_DEB) $(DIST_TAR) $(DIST_ZIP)

develop: tags
	$(PYTHON) $(PYFLAGS) setup.py develop

test:
	$(PYTHON) $(PYFLAGS) setup.py test

clean:
	$(PYTHON) $(PYFLAGS) setup.py clean
	$(MAKE) -f $(CURDIR)/debian/rules clean
	rm -fr build/ dist/ $(NAME).egg-info/ tags
	find $(CURDIR) -name "*.pyc" -delete

tags: $(PY_SOURCES)
	ctags -R --exclude="build/*" --exclude="debian/*" --exclude="docs/*" --languages="Python"

$(MAN_PAGES): $(DOC_SOURCES)
	$(PYTHON) $(PYFLAGS) setup.py build_sphinx -b man

$(DIST_TAR): $(PY_SOURCES)
	$(PYTHON) $(PYFLAGS) setup.py sdist --formats gztar

$(DIST_ZIP): $(PY_SOURCES)
	$(PYTHON) $(PYFLAGS) setup.py sdist --formats zip

$(DIST_EGG): $(PY_SOURCES)
	$(PYTHON) $(PYFLAGS) setup.py bdist_egg

$(DIST_RPM): $(PY_SOURCES) $(MAN_PAGES)
	$(PYTHON) $(PYFLAGS) setup.py bdist_rpm \
		--source-only \
		--doc-files README.rst,LICENSE.txt \
		--requires python,python-sqlalchemy
	# XXX Add man-pages to RPMs ... how?

$(DIST_DEB): $(PY_SOURCES) $(MAN_PAGES)
	# build the source package in the parent directory then rename it to
	# project_version.orig.tar.gz
	$(PYTHON) $(PYFLAGS) setup.py sdist --dist-dir=../
	rename -f 's/$(NAME)-(.*)\.tar\.gz/$(NAME)_$$1\.orig\.tar\.gz/' ../*
	debuild -b -i -I -Idist -Idocs -Ibuild/sphinx/doctrees -rfakeroot
	mkdir -p dist/
	cp ../$(NAME)_$(VER)-1~ppa1_all.deb dist/

release: $(PY_SOURCES) $(DOC_SOURCES)
	$(MAKE) clean
	# ensure there are no current uncommitted changes
	test -z "$(shell git status --porcelain)"
	# update the changelog with new release information
	dch --newversion $(VER)-1~ppa1 --controlmaint
	# commit the changes and add a new tag
	git commit debian/changelog -m "Updated changelog for release $(VER)"
	git tag -s release-$(VER) -m "Release $(VER)"

upload: $(PY_SOURCES) $(DOC_SOURCES)
	# build a source archive and upload to PyPI
	$(PYTHON) $(PYFLAGS) setup.py sdist upload
	# build the deb source archive
	$(PYTHON) $(PYFLAGS) setup.py build_sphinx -b man
	$(PYTHON) $(PYFLAGS) setup.py sdist --dist-dir=../
	rename -f 's/$(NAME)-(.*)\.tar\.gz/$(NAME)_$$1\.orig\.tar\.gz/' ../*
	debuild -S -i -I -Idist -Idocs -Ibuild/sphinx/doctrees -rfakeroot
	dput waveform-ppa ../$(NAME)_$(VER)-1~ppa1_source.changes

