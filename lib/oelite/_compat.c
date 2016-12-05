#include "Python.h"
#include <fcntl.h>

static PyMethodDef functions[] = {
	{0}
};

#ifndef PyModule_AddIntMacro
#define PyModule_AddIntMacro(m, c) PyModule_AddIntConstant(m, #c, c)
#endif

PyMODINIT_FUNC
init_compat(void)
{
	PyObject *mod = Py_InitModule3("_compat", functions,
				       "stuff not exposed directly by Python 2.7's stdlib");
	if (!mod)
		return;

#ifdef O_CLOEXEC
	(void) PyModule_AddIntMacro(mod, O_CLOEXEC);
#endif
#ifdef F_DUPFD_CLOEXEC
	(void) PyModule_AddIntMacro(mod, F_DUPFD_CLOEXEC);
#endif
}
