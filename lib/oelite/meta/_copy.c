/* gcc -Wall -Wextra -Werror -g -O2 -o _copy.so -fPIC -shared -I/usr/include/python2.7 _copy.c */
#include "Python.h"

/*
 * Py_[X]SETREF were apparently introduced somewhere between 2.7.5 and
 * 2.7.11, so provide definitions if they're missing.
 */

#ifndef Py_SETREF
#define Py_SETREF(op, op2)                      \
    do {                                        \
        PyObject *_py_tmp = (PyObject *)(op);   \
        (op) = (op2);                           \
        Py_DECREF(_py_tmp);                     \
    } while (0)
#endif

#ifndef Py_XSETREF
#define Py_XSETREF(op, op2)                     \
    do {                                        \
        PyObject *_py_tmp = (PyObject *)(op);   \
        (op) = (op2);                           \
        Py_XDECREF(_py_tmp);                    \
    } while (0)
#endif


/*
 * Duplicate of builtin_id. Looking around the CPython code base, it seems to
 * be quite common to just open-code this as PyLong_FromVoidPtr, though I'm
 * not sure those cases actually need to interoperate with Python code that
 * uses id(). We do, however, so it would be nicer there was an official
 * public API (e.g. PyObject_Id, maybe just a macro to avoid extra
 * indirection) providing this..
 */
static PyObject *
object_id(PyObject *v)
{
	return PyLong_FromVoidPtr(v);
}

static int
memo_keepalive(PyObject *x, PyObject *memo)
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

static PyObject *
do_deepcopy_fallback(PyObject *x, PyObject *memo)
{
	static PyObject *copymodule;
	if (copymodule == NULL) {
		copymodule = PyImport_ImportModule("copy");
		if (copymodule == NULL)
			return NULL;
	}
	assert(copymodule != NULL);

	return PyObject_CallMethod(copymodule, "_deepcopy_fallback", "OO", x, memo);
}

static PyObject *
deepcopy_list(PyObject *x, PyObject *memo, PyObject *id_x)
{
	PyObject *y, *elem;
	Py_ssize_t i, size;

	assert(PyList_CheckExact(x));
	size = PyList_GET_SIZE(x);

	/*
	 * Make a copy of x, then replace each element with its
	 * deepcopy. This avoids building the new list with repeated
	 * PyList_Append calls, and also avoids problems that could
	 * occur if some user-defined __deepcopy__ mutates the source
	 * list. However, this doesn't eliminate all possible
	 * problems, since Python code can still get its hands on y
	 * via the memo, so we're still careful to check 'i <
	 * PyList_GET_SIZE(y)' before getting/setting in the loop
	 * below.
	 */
	y = PyList_GetSlice(x, 0, size);
	if (y == NULL)
		return NULL;
	assert(PyList_CheckExact(y));

	if (PyDict_SetItem(memo, id_x, y) < 0) {
		Py_DECREF(y);
		return NULL;
	}

	for (i = 0; i < PyList_GET_SIZE(y); ++i) {
		elem = PyList_GET_ITEM(y, i);
		Py_INCREF(elem);
		Py_SETREF(elem, do_deepcopy(elem, memo));
		if (elem == NULL) {
			Py_DECREF(y);
			return NULL;
		}
		/*
		 * This really should not happen, but let's just
		 * return what's left in the list.
		 */
		if (i >= PyList_GET_SIZE(y)) {
			Py_DECREF(elem);
			break;
		}
		PyList_SetItem(y, i, elem);
	}
	return y;
}

static PyObject *
deepcopy_dict(PyObject *x, PyObject *memo, PyObject *id_x)
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
		Py_INCREF(key);
		Py_INCREF(val);

		Py_SETREF(key, do_deepcopy(key, memo));
		if (key == NULL) {
			Py_DECREF(y);
			Py_DECREF(val);
			return NULL;
		}

		Py_SETREF(val, do_deepcopy(val, memo));
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

static PyObject *
deepcopy_tuple(PyObject *x, PyObject *memo, PyObject *id_x)
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

	/*
	 * Three cases:
	 *
	 * (a) all members are "atomic", e.g. (1, "hello", None)
	 *
	 * (b) we've encountered the same tuple deeper down the call
	 * stack and memoized the result
	 *
	 * (c) this is the first time we see this tuple
	 *
	 * We could skip case (a), but there's no need to memoize such
	 * a tuple, and we save some memory by reusing the input
	 * x. This hurts a pathological case like [(1, 2, 3)]*10000,
	 * but we assume that those are rare, and memoizing would hurt
	 * the general case (by using more memory and making memo
	 * lookups slower).
	 */
	if (all_identical) {
		/* (a), reuse x */
		Py_INCREF(x);
		Py_DECREF(y);
		y = x;
	} else if ((z = PyDict_GetItem(memo, id_x)) != NULL) {
		/* (b), reuse the memo'ed tuple */
		Py_INCREF(z);
		Py_DECREF(y);
		y = z;
	} else {
		/* (c), memoize y for use by callers higher up as well
		 * as later calls */
		if (PyDict_SetItem(memo, id_x, y) < 0)
			Py_CLEAR(y);
	}
	return y;
}

#define INTERN_ALL_STRINGS 1
static PyTypeObject * const atomic_type[] = {
#if !INTERN_ALL_STRINGS
	&PyString_Type, /* str */
#endif
	&PyInt_Type, /* int */
	&PyUnicode_Type, /* unicode */
	&PyLong_Type, /* long */
	&PyBool_Type, /* bool */
	&PyFloat_Type, /* float */
	&PyComplex_Type, /* complex */
	&PyType_Type, /* type */
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

static PyObject *
do_deepcopy(PyObject *x, PyObject *memo)
{
	unsigned i;
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
#if INTERN_ALL_STRINGS
	if (Py_TYPE(x) == &PyString_Type) {
		Py_INCREF(x);
		PyString_InternInPlace(&x);
		return x;
	}
#endif
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
static PyObject *
deepcopy(PyObject *self, PyObject *args)
{
	PyObject *x, *memo = Py_None, *result;

	(void) self;
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
	{0}
};

PyMODINIT_FUNC
init_copy(void)
{
	(void) Py_InitModule3("_copy", functions, "C implementation of deepcopy");
}
