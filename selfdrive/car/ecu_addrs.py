#!/usr/bin/env python3
import capnp
import time

from panda.python.uds import SERVICE_TYPE
from openpilot.selfdrive.car import make_tester_present_msg, carlog
from openpilot.selfdrive.car.can_definitions import CanRecvCallable, CanSendCallable
from openpilot.selfdrive.car.fw_query_definitions import EcuAddrBusType


def _is_tester_present_response(msg: capnp.lib.capnp._DynamicStructReader, subaddr: int = None) -> bool:
  # ISO-TP messages are always padded to 8 bytes
  # tester present response is always a single frame
  dat_offset = 1 if subaddr is not None else 0
  if len(msg.dat) == 8 and 1 <= msg.dat[dat_offset] <= 7:
    # success response
    if msg.dat[dat_offset + 1] == (SERVICE_TYPE.TESTER_PRESENT + 0x40):
      return True
    # error response
    if msg.dat[dat_offset + 1] == 0x7F and msg.dat[dat_offset + 2] == SERVICE_TYPE.TESTER_PRESENT:
      return True
  return False


def _get_all_ecu_addrs(can_recv: CanRecvCallable, can_send: CanSendCallable, bus: int, timeout: float = 1, debug: bool = True) -> set[EcuAddrBusType]:
  addr_list = [0x700 + i for i in range(256)] + [0x18da00f1 + (i << 8) for i in range(256)]
  queries: set[EcuAddrBusType] = {(addr, None, bus) for addr in addr_list}
  responses = queries
  return get_ecu_addrs(can_recv, can_send, queries, responses, timeout=timeout, debug=debug)


def get_ecu_addrs(can_recv: CanRecvCallable, can_send: CanSendCallable, queries: set[EcuAddrBusType],
                  responses: set[EcuAddrBusType], timeout: float = 1, debug: bool = False) -> set[EcuAddrBusType]:
  ecu_responses: set[EcuAddrBusType] = set()  # set((addr, subaddr, bus),)
  try:
    msgs = [make_tester_present_msg(addr, bus, subaddr) for addr, subaddr, bus in queries]

    can_recv()
    can_send(msgs)
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
      can_packets = can_recv(wait_for_one=True)
      for packet in can_packets:
        for msg in packet:
          if not len(msg.dat):
            carlog.warning("ECU addr scan: skipping empty remote frame")
            continue

          subaddr = None if (msg.address, None, msg.src) in responses else msg.dat[0]
          if (msg.address, subaddr, msg.src) in responses and _is_tester_present_response(msg, subaddr):
            if debug:
              print(f"CAN-RX: {hex(msg.address)} - 0x{bytes.hex(msg.dat)}")
              if (msg.address, subaddr, msg.src) in ecu_responses:
                print(f"Duplicate ECU address: {hex(msg.address)}")
            ecu_responses.add((msg.address, subaddr, msg.src))
  except Exception:
    carlog.exception("ECU addr scan exception")
  return ecu_responses


if __name__ == "__main__":
  import argparse
  import cereal.messaging as messaging
  from openpilot.common.params import Params
  from openpilot.selfdrive.car.card import can_comm_callbacks, obd_callback

  parser = argparse.ArgumentParser(description='Get addresses of all ECUs')
  parser.add_argument('--debug', action='store_true')
  parser.add_argument('--bus', type=int, default=1)
  parser.add_argument('--no-obd', action='store_true')
  parser.add_argument('--timeout', type=float, default=1.0)
  args = parser.parse_args()

  logcan = messaging.sub_sock('can')
  sendcan = messaging.pub_sock('sendcan')
  can_callbacks = can_comm_callbacks(logcan, sendcan)

  # Set up params for pandad
  params = Params()
  params.remove("FirmwareQueryDone")
  params.put_bool("IsOnroad", False)
  time.sleep(0.2)  # thread is 10 Hz
  params.put_bool("IsOnroad", True)

  obd_callback(params)(not args.no_obd)

  print("Getting ECU addresses ...")
  ecu_addrs = _get_all_ecu_addrs(*can_callbacks, args.bus, args.timeout, debug=args.debug)

  print()
  print("Found ECUs on rx addresses:")
  for addr, subaddr, _ in ecu_addrs:
    msg = f"  {hex(addr)}"
    if subaddr is not None:
      msg += f" (sub-address: {hex(subaddr)})"
    print(msg)
