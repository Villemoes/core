require conf/u-boot.conf

inherit c make kernel-arch

# Why bother?  U-Boot will most likely stay broken for parallel builds
PARALLEL_MAKE = ""

EXTRA_OEMAKE = "ARCH=${UBOOT_ARCH} CROSS_COMPILE=${TARGET_PREFIX}"

CFLAGS[unexport]   = "1"
CPPFLAGS[unexport] = "1"
CXXFLAGS[unexport] = "1"
LDFLAGS[unexport]  = "1"

do_configure () {
    if [ ! -z '${USE_uboot_extra_env}' ]; then
      sed -i -e '/\#define[ ,\t]CONFIG_EXTRA_ENV_SETTINGS/,/^$/ s/[^\]$/& \\/' include/configs/${USE_uboot_config_file}
      sed -i -e '/\#define[ ,\t]CONFIG_EXTRA_ENV_SETTINGS/,/^$/ s/^$/& ${USE_uboot_extra_env} \n/' include/configs/${USE_uboot_config_file}
    fi
    oe_runmake ${USE_uboot_config}
}
oe_runmake[emit] += "do_configure"

do_compile () {
    oe_runmake ${UBOOT_IMAGE}
}

# Support checking the u-boot image size
inherit sizecheck
UBOOT_SIZECHECK = ""
UBOOT_SIZECHECK:USE_uboot_maxsize = "${UBOOT_IMAGE}:${USE_uboot_maxsize}"
SIZECHECK += "${UBOOT_SIZECHECK}"

do_install () {
    install -d ${D}${bootdir}
    install -m 0644 ${UBOOT_IMAGE} ${D}${bootdir}
    install -m 0644 ${UBOOT_IMAGE_BASE} ${D}${bootdir}
}

PACKAGES = "${PN} ${PN}-elf"
FILES_${PN} = "${bootdir}/${UBOOT_IMAGE_FILENAME}"
FILES_${PN}-elf = "${bootdir}/${UBOOT_IMAGE_BASE}"

addtask deploy before do_build after do_compile
do_deploy[dirs] = "${IMAGE_DEPLOY_DIR} ${S}"

do_deploy () {
    install -m 0644 ${UBOOT_IMAGE} \
	${IMAGE_DEPLOY_DIR}/${UBOOT_IMAGE_DEPLOY_FILE}
    md5sum <${UBOOT_IMAGE} \
	>${IMAGE_DEPLOY_DIR}/${UBOOT_IMAGE_DEPLOY_FILE}.md5

    cd ${IMAGE_DEPLOY_DIR}
    if [ -n "${UBOOT_IMAGE_DEPLOY_LINK}" ] ; then
	for ext in "" ".md5"; do
	    rm -f  ${UBOOT_IMAGE_DEPLOY_LINK}$ext
	    ln -sf ${UBOOT_IMAGE_DEPLOY_FILE}$ext \
		   ${UBOOT_IMAGE_DEPLOY_LINK}$ext
	done
    fi
}
