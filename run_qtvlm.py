import os
import subprocess
import time

QT_VLM_BIN = '~/bin/gtVlm/qtVlm.app/Contents/MacOS/qtVlm'


def run_qtvlm(xml_file, timeout_sec):
    process = subprocess.Popen([QT_VLM_BIN, xml_file],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)

    print(f'Started qtVlm with PID {process.pid}')
    time.sleep(5)  # Get time to spin
    started_at = time.time()
    while True:
        time.sleep(5)
        stream = os.popen(f'ps {process.pid} -o %cpu')
        output = stream.read()
        t = output.split('\n')
        cpu_use = 0
        if len(t) >= 2:
            cpu_use = float(t[1])
        print(f'CPU use: {cpu_use}')
        if cpu_use < 10:
            print('qtVlm is idle')
            break
        if time.time() - started_at > timeout_sec:
            print('Timeout expired')
            break

    print('Quitting from qtVlm ...')
    stream = os.popen(f'osascript close_qtvlm.scpt')
    output = stream.read()
    print(output)

    print('waiting for qtVlm to terminate...')
    process.wait()


if __name__ == '__main__':
    run_qtvlm('qt_vlm_xml/qtvlm_all_routes.xml', 120)
