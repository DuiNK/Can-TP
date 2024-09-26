import can
import threading
import time
from ics import ics
from enum import Enum
 
Max_buffer = 10000

class FlowStatus(Enum):
    CTS = 0
    WAIT = 1
    OVFLW = 2
 
class CanTP(can.Listener):
    def __init__(self, can_bus, chunk_size, fd):
        self.can_bus = can_bus
        self.fd = fd                        #flex data
        self.fs = FlowStatus.CTS            #flow status
        self.buffer = b""
        self.sequence_number = 0
        self.bs = 4                         #block size
        self.stMin = 0.01                   #separation time
        self.expected_sequence = 1
        self.timeout = 1
        self.expected_length = 0
        self.running = threading.Event()
        self.running.set()
        self.chunk_size = chunk_size
        self.cf_flag = 0
 
    def send(self, data):
        can_dl = len(data)
        if can_dl < self.chunk_size:
            self._send_single_frame(data)
        else:
            self._send_multi_frame(data)
   
    def _send_single_frame(self, data):
        if self.chunk_size <= 8:
            frame = can.Message(data=bytes([len(data)]) + data, is_extended_id=False, is_fd=self.fd, bitrate_switch=self.fd)
            print("Sending SF: " + str(frame))
            self.can_bus.send(frame)
        else:
            frame = can.Message(data=bytes([0x00, len(data) & 0xFF]) + data[:self.chunk_size], is_extended_id=False, is_fd=self.fd, bitrate_switch=self.fd)
            print("Sending SF: " + str(frame))
            self.can_bus.send(frame)
   
    def wait_to_cts(self):
        time_N_Bs = time.time()
        while self.fs == FlowStatus.WAIT:
            if self.fs == FlowStatus.CTS:
                break
            if time.time() - time_N_Bs > self.timeout:
                self.running.clear()
                raise TimeoutError("Timeout waiting for CTS after send CF")
 
    def _send_multi_frame(self, data):
        can_dl = len(data)
        if can_dl < 4096:
            first_frame_data = bytes([0x10 | (can_dl >> 8), can_dl & 0xFF]) + data[:self.chunk_size - 2]
            frame = can.Message(data=first_frame_data, is_extended_id=False, is_fd=self.fd, bitrate_switch=self.fd)
            print("Sending FF: " + str(frame))
            self.can_bus.send(frame)
            self.fs = FlowStatus.WAIT
           
            # Wait for flow control: first CTS
            self.wait_to_cts()
           
            # Consecutive frames
            data_index = self.chunk_size - 2
            self.sequence_number = 1
            while self.fs == FlowStatus.CTS:
                if data_index > can_dl:
                    data_index = 0
                    print("Done")
                    break
                print("consecutive frames")
                print(self.fs)
                for i in range(self.bs):                        
                    # Send consicutive frame
                    cf_data = bytes([0x20 | self.sequence_number]) + data[data_index:(data_index+self.chunk_size-1)]
                    print(f"{cf_data}")
                    cf_message = can.Message(data=cf_data, is_extended_id=False, is_fd=self.fd, bitrate_switch=self.fd)
                    print(f"{str(cf_message)}")
                    data_index += (self.chunk_size - 1)
                    self.sequence_number = (self.sequence_number + 1) % 16
                    self.can_bus.send(cf_message)
                    # check data_index
                    if data_index >= can_dl:
                        break
                    # delay separation time                    
                    time.sleep(self.stMin / 1000)  # Convert ms to seconds
                if data_index >= can_dl:
                    data_index = 0
                    print("All Done")
                    break
                self.fs = FlowStatus.WAIT
                self.wait_to_cts()
 
        else:
            first_frame_data = bytes([0x10, 0x00, can_dl & 0xFFFF]) + data[:self.chunk_size - 6]
            frame = can.Message(data=first_frame_data, is_extended_id=False, is_fd=self.fd, bitrate_switch=self.fd)
            print("Sending FF: " + str(frame))
            self.can_bus.send(frame)
            self.fs = FlowStatus.WAIT
            # Wait for flow control: first CTS
            time_N_Bs = time.time()
            while self.fs == FlowStatus.WAIT:
                if time.time() - time_N_Bs > self.timeout:
                    self.running.clear()
                    raise TimeoutError("Timeout waiting for CTS")
           
            # Consecutive frames
            data_index = self.chunk_size - 6
            self.sequence_number = 1
            while self.fs == FlowStatus.CTS:
                if data_index > can_dl:
                    data_index = 0
                    print(f'Done')
                    break
                for i in self.bs:                        
                    # Send consicutive frame
                    cf_data = bytes([0x20 | self.sequence_number]) + data[data_index:data_index+self.chunk_size-1]
                    cf_message = can.Message(data=cf_data, is_extended_id=False, is_fd=self.fd, bitrate_switch=self.fd)
                    self.can_bus.send(cf_message)
 
                    data_index += (self.chunk_size - 1)
                    self.sequence_number = (self.sequence_number + 1) % 16
                    # delay separation time
                    time.sleep(self.stMin / 1000)  # Convert ms to seconds              
                if data_index >= can_dl:
                    data_index = 0
                    print("All Done")
                    break
                self.fs == FlowStatus.WAIT
                self.wait_to_cts()
 
    def stop(self):
        self.running.clear()
 
    def reconnect(self):
        try:
            self.can_bus.shutdown()
        except:
            pass
       
        max_attempts = 1
        for attempt in range(max_attempts):
            try:
                self.can_bus = can_bus
                print("Successfully reconnected to CAN bus")
                return
            except can.exceptions.CanError:
                print(f"Reconnection attempt {attempt + 1} failed. Retrying...")
                time.sleep(1)
       
        print("Failed to reconnect after multiple attempts")
        self.stop()  
   
    def on_message_received(self, frame: can.Message):
        frame_type = frame.data[0] & 0xF0
        if   frame_type == 0x00:  # Single frame
            self._process_single_frame(frame)
        elif frame_type == 0x10:  # First frame
            self._process_first_frame(frame)
        elif frame_type == 0x20:  # Consecutive frame
            self._process_consecutive_frame(frame)
        elif frame_type == 0x30:  # Flow control
            self._process_flow_control(frame)
        else :
            print(f"Unsupported frame type: {frame_type:02X}")
            self.buffer = b""
            self.reconnect()
 
    def _process_single_frame(self, frame):
        if frame.data[0] & 0x0F != 0x00:
            print("single")
            self.expected_length = frame.data[0] & 0x0F
            self.buffer = frame.data[1:1+self.expected_length]
            self._message_complete()
        else:
            self.expectec_length = frame.data[1] & 0xFF
            self.buffer = frame.data[1:1+self.expected_length]
            self._message_complete()
   
    def _process_first_frame(self, frame):
        if (frame.data[1] != 0x00) or (frame.data[0] & 0x0F != 0x00):
            self.expected_length = ((frame.data[0] & 0x0F) << 8) | frame.data[1]
            self.buffer = frame.data[2:]
            self.sequence_number = 1
           
            # Send flow control
            fc_frame = can.Message(data=bytes([0x30, self.bs, int(self.stMin * 100)]), is_extended_id=False, is_fd=self.fd, bitrate_switch=self.fd)
            print(f"send first fc frame: {fc_frame}")
            self.can_bus.send(fc_frame)
        else:
            self.expected_length = frame.data[2] | frame.data[3] |frame.data[4] |frame.data[5]
            self.buffer = frame.data[6:]
            self.sequence_number = 1
           
            # Send flow control
            fc_frame = can.Message(data=bytes([0x30, self.bs, int(self.stMin * 100)]), is_extended_id=False, is_fd=self.fd, bitrate_switch=self.fd)
            print(f"send first fc frame 2: {fc_frame}")
            self.can_bus.send(fc_frame) 
        self.cf_flag = 0
    
    def _process_consecutive_frame(self, frame):
        self.expected_sequence = frame.data[0] & 0x0F
        if self.sequence_number != self.expected_sequence:
            print(f"Received out-of-sequence frame. Expected {self.expected_sequence}, got {self.sequence_number}")
            self.buffer = b""
            return
        self.buffer += frame.data[1:]
        self.cf_flag += (self.chunk_size - 1)
        self.sequence_number = (self.sequence_number + 1) % 16
        if len(self.buffer) > Max_buffer:
            time.sleep(0.05)
            # Send Flow status : OverFlow 
            fc_frame = can.Message(data=bytes([0x32, self.bs, int(self.stMin * 100)]), is_extended_id=False, is_fd=self.fd, bitrate_switch=self.fd)
            self.can_bus.send(fc_frame)
            self.cf_flag = 0
            print("Over Flow!!!!")
            print(f"FlowControl: {str(fc_frame)}")
        if len(self.buffer) >= self.expected_length:
            self._message_complete()
        elif (self.cf_flag) % (self.bs * (self.chunk_size - 1)) == 0:
            # Send flow control
            time.sleep(0.05)
            fc_frame = can.Message(data=bytes([0x30, self.bs, int(self.stMin * 100)]), is_extended_id=False, is_fd=self.fd, bitrate_switch=self.fd)
            self.can_bus.send(fc_frame)
            self.cf_flag = 0
            print(f"FlowControl: {str(fc_frame)}")

    def _process_flow_control(self, frame):
        self.fs = FlowStatus(frame.data[0] & 0x0F)
        print(f"receive {self.fs.name}")
        if self.fs == FlowStatus.CTS:
            self.bs = frame.data[1]
            self.stMin = frame.data[2] / 100.0
 
        elif self.fs == FlowStatus.WAIT:
            time.sleep(0.5)
        elif self.fs == FlowStatus.OVFLW:
            self.running.clear()
            raise Exception("Receiver reported overflow")
        else:
            raise Exception("Unexpected flow control frame")

 
    def _message_complete(self):
        print(f"Received message: {self.buffer[:self.expected_length]}")
        self.buffer = b""
 
 
if __name__ == "__main__":
    can_interface = 'neovi'
    channel = '1'  
    bitrate = '1000000'
    fd = False
    chunk_size = 8
    arbitration_id = 000
 
    can_bus = can.interface.Bus(arbitration_id=arbitration_id, channel=channel, interface=can_interface, bitrate=bitrate, fd=fd, receive_own_messages= False)
 
    cantp = CanTP(can_bus, chunk_size, fd)
    notifier = can.Notifier(can_bus, [cantp])
    
    # Send
    try:
        while True:
            message = input("Enter message to send (or 'q' to quit): ")
            if message.lower() == 'q':
                break
            try:
                cantp.send(message.encode())
            except Exception as e:
                print(f"Error sending message: {e}")

    # Receive
    # try:
    #     print("Listening for CanTP message")
    #     while True:
    #         time.sleep(1)
    
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        notifier.stop()
        can_bus.shutdown()        