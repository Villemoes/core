/* gcc -g -O2 -o _copy.so -fPIC -shared -I/usr/include/python2.7 _copy.c -fno-stack-protector */
#include "Python.h"

/*
 * There's a slightly incestuous relationship between this and
 * copy.py, since we need to call functions from each other. Oh
 * well. For now, we just import copy during our init function. We
 * might optimize that by looking up and caching _deepcopy_fallback,
 * but then we'd also have to ensure that any import of _copy from
 * copy.py happens after _deepcopy_fallback is defined.
 */
static PyObject *copymodule;

/*
 * It seems silly to have to call builtin_id via its Python wrapper
 * (and first having to look it up via the __builtin__ module), so we
 * just duplicate its implementation. Looking around the CPython code
 * base, it seems to be quite common to just open-code this as
 * PyLong_FromVoidPtr, though I'm not sure those cases actually need
 * to interoperate with Python code that uses id(). We do, however, so
 * it would be nicer if this was defined (maybe just as a macro),
 * e.g. PyObject_Id.
 */

static PyObject *object_id(PyObject *v)
{
	return PyLong_FromVoidPtr(v);
}

static int memo_keepalive(PyObject *x, PyObject *memo)
{
	PyObject *memoid, *list;
	int ret;

	memoid = object_id(memo);
	if (memoid == NULL)
		return -1;

	/* try: memo[id(memo)].append(x) */
	list = PyDict_GetItem(memo, memoid);
	if (list != NULL) {
		Py_DECREF(memoid);
		return PyList_Append(list, x);
	}

	/* except KeyError: memo[id(memo)] = [x] */
	list = PyList_New(1);
	if (list == NULL) {
		Py_DECREF(memoid);
		return -1;
	}
	Py_INCREF(x);
	PyList_SET_ITEM(list, 0, x);
	ret = PyDict_SetItem(memo, memoid, list);
	Py_DECREF(memoid);
	Py_DECREF(list);
	return ret;
}

/* Forward declaration. */
static PyObject *do_deepcopy(PyObject *x, PyObject *memo);

static PyObject *do_deepcopy_fallback(PyObject *x, PyObject *memo)
{
	assert(copymodule != NULL);

	return PyObject_CallMethod(copymodule, "_deepcopy_fallback", "OO", x, memo);
}

static PyObject *deepcopy_list(PyObject *x, PyObject *memo, PyObject *id_x)
{
	PyObject *y, *elem;
	Py_ssize_t i, size;

	assert(PyList_CheckExact(x));
	size = PyList_Size(x);

	y = PyList_New(size);
	if (y == NULL)
		return NULL;

	/*
	 * We have to store the new object in the memo before filling it with
	 * values, but since we can easily end up calling back to Python code
	 * while recursing into the data structure, we should probably make
	 * sure that it is at all times a valid list (even though nobody
	 * should actually look at it). So fill it with None. Doing this extra
	 * one pass (and corresponding DECREFs) should still be cheaper than
	 * building the list with repeated PyList_Append calls.
	 */
	for (i = 0; i < size; ++i) {
		Py_INCREF(Py_None);
		PyList_SET_ITEM(y, i, Py_None);
	}


	if (PyDict_SetItem(memo, id_x, y) < 0) {
		Py_DECREF(y);
		return NULL;
	}

	for (i = 0; i < size; ++i) {
		/* XXX: Should we handle x getting mutated while we're
		 * iterating? There's at least two things that could be
		 * wrong here: The size changing, and the borrowed ref
		 * elem becoming dangling before our immediate callee
		 * do_deepcopy is done using it. Hmm... */
		elem = PyList_GET_ITEM(x, i);
		elem = do_deepcopy(elem, memo);
		if (elem == NULL) {
			Py_DECREF(y);
			return NULL;
		}
		assert(PyList_GET_ITEM(y, i) == Py_None);
		Py_DECREF(Py_None);
		PyList_SET_ITEM(y, i, elem);
	}
	return y;
}

static PyObject *deepcopy_dict(PyObject *x, PyObject *memo, PyObject *id_x)
{
	PyObject *y, *key, *val;
	Py_ssize_t pos, size;
	int ret;

	assert(PyDict_CheckExact(x));
	size = PyDict_Size(x);

	y = _PyDict_NewPresized(size);
	if (y == NULL)
		return NULL;

	if (PyDict_SetItem(memo, id_x, y) < 0) {
		Py_DECREF(y);
		return NULL;
	}

	for (pos = 0; PyDict_Next(x, &pos, &key, &val); ) {
		key = do_deepcopy(key, memo);
		if (key == NULL) {
			Py_DECREF(y);
			return NULL;
		}
		val = do_deepcopy(val, memo);
		if (val == NULL) {
			Py_DECREF(y);
			Py_DECREF(key);
			return NULL;
		}
		ret = PyDict_SetItem(y, key, val);
		Py_DECREF(key);
		Py_DECREF(val);
		if (ret < 0) {
			/* Shouldn't happen - y is presized */
			Py_DECREF(y);
			return NULL;
		}
	}

	return y;
}

static PyObject *deepcopy_tuple(PyObject *x, PyObject *memo, PyObject *id_x)
{
	PyObject *y, *z, *elem, *copy;
	Py_ssize_t i, size;
	int all_identical = 1; /* are all members their own deepcopy? */

	assert(PyTuple_CheckExact(x));
	size = PyTuple_GET_SIZE(x);

	y = PyTuple_New(size);
	if (y == NULL)
		return NULL;

	/*
	 * We cannot add y to the memo just yet (even if we
	 * Py_None-initialized it), since Python code would then be able to
	 * observe a tuple with values changing. We do, however, have an
	 * advantage over the Python implementation in that we can actually
	 * build the tuple directly instead of using an intermediate list
	 * object.
	 */
	for (i = 0; i < size; ++i) {
		elem = PyTuple_GET_ITEM(x, i);
		copy = do_deepcopy(elem, memo);
		if (copy == NULL) {
			Py_DECREF(y);
			return NULL;
		}
		if (copy != elem)
			all_identical = 0;
		PyTuple_SET_ITEM(y, i, copy);
	}

	/* Did we do a copy of the same tuple deeper down? */
	z = PyDict_GetItem(memo, id_x);
	if (z != NULL) {
		Py_INCREF(z);
		Py_DECREF(y);
		return z;
	}

	if (all_identical) {
		Py_INCREF(x);
		Py_DECREF(y);
		y = x;
	}

	/* OK, make sure any of our callers up the stack return this copy. */
	if (PyDict_SetItem(memo, id_x, y) < 0) {
		Py_DECREF(y);
		return NULL;
	}
	return y;
}

/* This needs some ifdeffery to work for both 2 and 3 */
static PyTypeObject * const atomic_type[] = {
	&PyString_Type,
	&PyBool_Type,
	&PyInt_Type,
	&PyLong_Type,
	&PyFloat_Type,
	&PyType_Type,
};
#define N_ATOMIC_TYPES (sizeof(atomic_type)/sizeof(atomic_type[0]))

struct deepcopy_dispatcher {
	PyTypeObject *type;
	PyObject * (*handler)(PyObject *x, PyObject *memo, PyObject *id_x);
};

static const struct deepcopy_dispatcher deepcopy_dispatch[] = {
	{&PyList_Type, deepcopy_list},
	{&PyDict_Type, deepcopy_dict},
	{&PyTuple_Type, deepcopy_tuple},
};
#define N_DISPATCHERS (sizeof(deepcopy_dispatch)/sizeof(deepcopy_dispatch[0]))

static PyObject *do_deepcopy(PyObject *x, PyObject *memo)
{
	int i;
	PyObject *y, *id_x;
	const struct deepcopy_dispatcher *dd;

	assert(PyDict_CheckExact(memo));

	/*
	 * No need to have a separate dispatch function for this. Also, the
	 * array would have to be quite a lot larger before a smarter data
	 * structure is worthwhile. Sad that PyNone_Type is not exposed.
	 */
	if (x == Py_None) {
		Py_INCREF(x);
		return x;
	}
	for (i = 0; i < N_ATOMIC_TYPES; ++i) {
		if (Py_TYPE(x) == atomic_type[i]) {
			Py_INCREF(x);
			return x;
		}
	}

	/* Have we already done a deepcopy of x? */
	id_x = object_id(x);
	if (id_x == NULL)
		return NULL;

	y = PyDict_GetItem(memo, id_x);
	if (y != NULL) {
		Py_DECREF(id_x);
		Py_INCREF(y);
		return y;
	}
	/*
	 * Hold on to id_x a little longer - the dispatch handlers will all
	 * need it.
	 */
	for (i = 0; i < N_DISPATCHERS; ++i) {
		dd = &deepcopy_dispatch[i];
		if (Py_TYPE(x) != dd->type)
			continue;

		y = dd->handler(x, memo, id_x);
		Py_DECREF(id_x);
		if (y == NULL)
			return NULL;
		if (x != y && memo_keepalive(x, memo) < 0) {
			Py_DECREF(y);
			return NULL;
		}
		return y;
	}

	Py_DECREF(id_x);

	return do_deepcopy_fallback(x, memo);
}

/*
 * This is the Python entry point. Hopefully we can stay in the C code
 * most of the time, but we will occasionally call the Python code to
 * handle the stuff that's very inconvenient to write in C, and that
 * will then call back to us.
 */
static PyObject *deepcopy(PyObject *self, PyObject *args)
{
	PyObject *x, *memo = Py_None, *result;

	/*
	 * copy.deepcopy has two optional and entirely internal
	 * arguments. We also don't need the _nil dummy, as we can
	 * easily recognize "not found in memo" without that. So just
	 * declare this as taking one or two positional arguments.
	 */
	if (!PyArg_UnpackTuple(args, "deepcopy", 1, 2, &x, &memo))
		return NULL;

	if (memo == Py_None) {
		memo = PyDict_New();
		if (memo == NULL)
			return NULL;
	} else {
		if (!PyDict_CheckExact(memo)) {
			PyErr_SetString(PyExc_TypeError, "memo must be a dict");
			return NULL;
		}
		Py_INCREF(memo);
	}

	result = do_deepcopy(x, memo);

	Py_DECREF(memo);
	return result;
}

static PyMethodDef functions[] = {
	{"deepcopy", deepcopy, METH_VARARGS, "Do a deep copy"},
	{NULL, NULL}
};

#if PY_MAJOR_VERSION >= 3
static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT,
        "_copy",
        "C implementation of deepcopy",
        -1,
        functions,
        NULL,
        NULL,
        NULL,
        NULL
};
#endif

#if PY_MAJOR_VERSION >= 3
#define INITERROR return NULL
PyMODINIT_FUNC
PyInit__copy(void)
#else
#define INITERROR return
void
init_copy(void)
#endif
{
#if PY_MAJOR_VERSION >= 3
	PyObject *module = PyModule_Create(&moduledef);
#else
	PyObject *module = Py_InitModule("_copy", functions);
#endif
	if (module == NULL)
		INITERROR;

	copymodule = PyImport_ImportModule("copy");
	if (copymodule == NULL) {
		Py_DECREF(module);
		INITERROR;
	}

#if PY_MAJOR_VERSION >= 3
	return module;
#endif
}
