# Firmware for the Tang Nano 4K LiteX SoC (Cortex-M3 / Gowin EMCU).
#
# Everything SoC-specific comes from the LiteX build:
#   variables.mak  -> compiler triple, CPU flags, library paths
#   common.mak     -> the compile/assemble rules ($(compile), $(assemble))
#   generated/*.h  -> csr.h, soc.h, mem.h  (register map, constants)
#   regions.ld     -> where rom/sram are (used by linker.ld)
# So: rebuild the SoC (python3 tang4k.py ...) -> just run `make` again here.

BUILD_DIR ?= ../build/sipeed_tang_nano_4k

include $(BUILD_DIR)/software/include/generated/variables.mak
include $(SOC_DIRECTORY)/software/common.mak

TARGET  = firmware
OBJECTS = crt0.o main.o capture.o         # crt0.o = M3 startup (vector table, stack, .data/.bss)

all: $(TARGET).bin size

# LiteX's prebuilt libraries (libbase: printf/uart, libc, ...)
LIBDIRS = $(PACKAGES:%=-L$(BUILD_DIR)/software/%)

$(TARGET).elf: $(OBJECTS) linker.ld
	$(CC) $(LDFLAGS) -T linker.ld -N -o $@ $(OBJECTS) $(LIBDIRS) \
		-Wl,--start-group $(LIBS:lib%=-l%) -Wl,--end-group \
		-Wl,--gc-sections -Wl,-Map,$@.map

%.bin: %.elf
	$(OBJCOPY) -O binary $< $@

size: $(TARGET).elf
	@$(TARGET_PREFIX)size $<

# crt0.c lives in the CPU directory of LiteX
VPATH = $(CPU_DIRECTORY)

%.o: %.c
	$(compile)

%.o: %.S
	$(assemble)

-include $(OBJECTS:.o=.d)

clean:
	$(RM) $(OBJECTS) $(OBJECTS:.o=.d) $(TARGET).elf $(TARGET).elf.map $(TARGET).bin

.PHONY: all clean size
