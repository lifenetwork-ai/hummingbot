from threading import Event

from setuptools import Distribution, Extension
from setuptools._distutils.ccompiler import new_compiler
from setuptools.command.build_ext import build_ext

from setup import BuildExt


def test_parallel_build_serializes_extensions_sharing_an_object_through_link(monkeypatch, tmp_path):
    shared = 'hummingbot/core/cpp/OrderBookEntry.cpp'
    order_book = Extension('order_book', ['order_book.cpp', shared])
    composite = Extension('composite_order_book', ['composite_order_book.cpp', shared])
    independent = Extension('unrelated', ['unrelated.cpp'])
    command = BuildExt(Distribution({'ext_modules': [order_book, composite, independent]}))
    command.ensure_finalized()
    command.parallel = 3
    command.build_temp = str(tmp_path)
    command.compiler = new_compiler()

    linking = Event()
    independent_started = Event()
    composite_started = Event()

    def build(self, extension):
        if extension is order_book:
            # The first compilation has written its object file. The linker
            # must be able to read it before another compiler can rewrite it.
            linking.set()
            assert independent_started.wait(3), 'Independent extensions must still build in parallel'
            try:
                assert not composite_started.wait(.25), 'A second build can overwrite the object while it is being linked'
            finally:
                linking.clear()
        elif extension is composite:
            composite_started.set()
        else:
            assert linking.wait(3)
            independent_started.set()

    monkeypatch.setattr(build_ext, 'build_extension', build)
    run_extension = command.build_extension

    def start(extension):
        if extension is composite:
            assert linking.wait(3)
        run_extension(extension)

    monkeypatch.setattr(command, 'build_extension', start)
    command.build_extensions()
    assert composite_started.is_set()
