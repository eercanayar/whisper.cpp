import wave
import zmq
import struct
import time
import sys

def send_audio(wav_path, chunk_size=32000):  # 2000ms chunks at 16kHz
    context = zmq.Context()
    socket = context.socket(zmq.PUSH)
    socket.connect("ipc:///tmp/whisper_audio.sock")

    with wave.open(wav_path, 'rb') as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        framerate = wav_file.getframerate()
        n_frames = wav_file.getnframes()

        print(f"WAV file info:")
        print(f"Channels: {channels}")
        print(f"Sample width: {sample_width} bytes")
        print(f"Sample rate: {framerate} Hz")
        print(f"Number of frames: {n_frames}")
        print(f"Duration: {n_frames / framerate:.2f} seconds")
        
        # Calculate sleep time between chunks
        chunk_duration = chunk_size / framerate  # duration of each chunk in seconds
        
        while True:
            chunk_start_time = time.time()
            
            raw_data = wav_file.readframes(chunk_size)
            if not raw_data:
                break
                
            float_data = []
            
            if sample_width == 2:
                format_str = '<h'
                scale = 32768.0
            elif sample_width == 4:
                format_str = '<l'
                scale = 2147483648.0
            else:
                raise ValueError(f"Unsupported sample width: {sample_width}")
            
            for i in range(0, len(raw_data), sample_width):
                sample = struct.unpack(format_str, raw_data[i:i+sample_width])[0]
                float_data.append(float(sample) / scale)
            
            if channels == 2:
                mono_data = []
                for i in range(0, len(float_data), 2):
                    if i+1 < len(float_data):
                        mono_data.append((float_data[i] + float_data[i+1]) / 2.0)
                    else:
                        mono_data.append(float_data[i])
                float_data = mono_data

            float_bytes = struct.pack(f'<{len(float_data)}f', *float_data)
            socket.send(float_bytes)
            
            # Calculate how long to sleep
            processing_time = time.time() - chunk_start_time
            sleep_time = chunk_duration - processing_time
            print(f"sleeping for {sleep_time}")
            if sleep_time > 0:
                time.sleep(sleep_time)

    socket.close()
    context.term()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python send_audio.py <wav_file>")
        sys.exit(1)
        
    wav_path = sys.argv[1]
    send_audio(wav_path)