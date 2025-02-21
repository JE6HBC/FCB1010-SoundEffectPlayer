import sys
import os
import time
import random
import threading
import mido
import pygame
from pygame import mixer
from PyQt5 import QtCore, QtGui, QtWidgets

# --- 定数 ---
REFRESH_RATE = 30  # 画面更新頻度(Hz)
VOLUME_CHANGE_STEP = 0.05  # ボリューム変更の刻み幅
DEFAULT_VOLUME = 0.7 # デフォルトの音量

# --- グローバル変数 ---
midi_in = None
audio_output_device = None
selected_midi_input_index = -1
selected_audio_output_index = -1
current_playing = {}  # {switch_number: (filename, channel)}
queued_sounds = {} # {switch_number: filename}  # 再生待ちのサウンド
volume = DEFAULT_VOLUME
midi_input_level = 0
audio_output_level = 0
lock = threading.Lock()  # スレッド間の排他制御用

# --- MIDI関連の関数 ---

def get_midi_inputs():
    """利用可能なMIDI入力デバイスのリストを取得"""
    return mido.get_input_names()

def open_midi_input(port_name):
    """MIDI入力を開く"""
    global midi_in
    try:
        midi_in = mido.open_input(port_name)
        return True
    except Exception as e:
        print(f"Error opening MIDI input: {e}")
        return False

def close_midi_input():
    """MIDI入力を閉じる"""
    global midi_in
    if midi_in:
        midi_in.close()

# --- オーディオ関連の関数 ---

def get_audio_outputs():
    """利用可能なオーディオ出力デバイスのリストを取得"""
    pygame.init()
    mixer.init()
    num_devices = mixer.get_num_channels() # get_num_channels() でデバイス数を取得できる
    device_names = []

    # get_num_channels() は実際のデバイス数より大きい値を返すことがあるので、
    # 実際に初期化を試みてエラーが出ないデバイスのみをリストに追加する
    for i in range(num_devices):
        try:
            mixer.init(44100, -16, 2, 512, devicename=str(i))  # 初期化を試みる
            mixer.quit() # エラーが出なければ、一旦閉じる
            device_names.append(pygame.mixer.get_init()[0]) # init() はタプルで返すので[0]でサンプリング周波数を返す
        except pygame.error:
            pass
    
    pygame.init() # 再初期化
    mixer.init() # 再初期化
    return device_names


def init_audio(device_index=None):
    """オーディオを初期化"""
    global audio_output_device
    pygame.init()

    if device_index is not None and device_index < pygame.mixer.get_num_channels():
        try:
           mixer.init(44100,-16, 2, 512, devicename=str(device_index)) # 44100Hz, 16bit, stereo, buffer=512 samples, 指定のデバイス
        except pygame.error as message:
            print ("Cannot initialize audio device.", message)
            mixer.init() # デフォルトデバイスで初期化
    else:
        mixer.init() # デフォルトデバイスで初期化

    audio_output_device = pygame.mixer.get_init()[0]

def play_sound(filename, channel_num):
    """指定されたチャンネルでサウンドを再生"""
    try:
        sound = mixer.Sound(filename)
        channel = mixer.Channel(channel_num)
        channel.play(sound, loops=-1)  # ループ再生
        channel.set_volume(volume)

        # 再生終了コールバックを設定
        channel.set_endevent(pygame.USEREVENT + channel_num) # チャンネルごとに固有のイベント

        return sound, channel
    except Exception as e:
        print(f"Error playing sound: {e}")
        return None, None

def stop_sound(channel):
    """指定されたチャンネルのサウンドを停止"""
    if channel:
        channel.stop()

def set_volume(val):
    """全体の音量を設定"""
    global volume
    volume = max(0.0, min(1.0, val))  # 0.0から1.0の範囲に制限
    for _, (_, channel) in current_playing.items():
        if channel:
            channel.set_volume(volume)

# --- MIDI/キーボード入力処理 ---
def process_midi_message():
    """MIDIメッセージを処理するスレッド"""
    global midi_input_level, current_playing, audio_output_level, queued_sounds

    if not midi_in:
      return

    for msg in midi_in.iter_pending():
        if msg.type == 'control_change':
            midi_input_level = msg.value / 127.0  # MIDIレベルを更新

            if msg.control >= 1 and msg.control <= 10:  # スイッチ1-10
                switch_number = msg.control
                process_input(switch_number, msg.value) # MIDIとキーボードで共通の処理

            elif msg.control == 7:  # エクスプレッションペダル (CC#7)
                set_volume(msg.value / 127.0)

def process_keyboard_input(key_event):
    """キーボード入力を処理"""
    if key_event.type == pygame.KEYDOWN:
        if key_event.unicode.isdigit():
            switch_number = int(key_event.unicode)
            if 1 <= switch_number <= 10:
                process_input(switch_number, 127) # キー押下をvalue=127として扱う
    elif key_event.type == pygame.KEYUP:
         if key_event.unicode.isdigit():
            switch_number = int(key_event.unicode)
            if 1 <= switch_number <= 10:
                process_input(switch_number, 0) # キー押下をvalue=0として扱う

def process_input(switch_number, value):
    """MIDI/キーボード入力共通の処理"""
    folder_path = str(switch_number)

    if os.path.isdir(folder_path):
        files = [f for f in os.listdir(folder_path) if f.endswith(('.wav', '.mp3', '.ogg'))]
        if files:
            if value > 0:  # スイッチ/キーが押された
                filename = random.choice(files)
                with lock:
                    # 既に再生中の場合はキューに追加
                    if switch_number in current_playing:
                        queued_sounds[switch_number] = filename
                    else:
                        # 再生中でなければ即座に再生
                        full_path = os.path.join(folder_path, filename)
                        sound, channel = play_sound(full_path, switch_number - 1)
                        if sound:
                            current_playing[switch_number] = (filename, channel)
            else:  # スイッチ/キーが離された
                with lock:
                    if switch_number in current_playing:
                        # キューに何かあれば、再生を予約
                        if switch_number in queued_sounds:
                            del queued_sounds[switch_number]
                    else:
                        if switch_number in current_playing:
                            _, channel = current_playing[switch_number]
                            if channel:
                                channel.fadeout(500)

# --- Pygameイベント処理 ---
def process_pygame_events():
    """Pygameのイベント(音声再生終了、キーボード入力)を処理する"""
    global current_playing, queued_sounds

    for event in pygame.event.get():
        if event.type >= pygame.USEREVENT and event.type < pygame.USEREVENT + 10:  # USEREVENTからUSEREVENT+9まで
            channel_num = event.type - pygame.USEREVENT
            switch_number = channel_num + 1

            with lock:
                if switch_number in current_playing:
                  del current_playing[switch_number]  # 再生終了したエントリを削除

                # キューに次のサウンドがあれば再生
                if switch_number in queued_sounds:
                    filename = queued_sounds.pop(switch_number)
                    folder_path = str(switch_number)
                    full_path = os.path.join(folder_path, filename)
                    sound, channel = play_sound(full_path, channel_num)
                    if sound:
                        current_playing[switch_number] = (filename, channel)

        # キーボード入力イベントの処理
        elif event.type == pygame.KEYDOWN or event.type == pygame.KEYUP:
            process_keyboard_input(event)

# --- GUI関連のクラス ---

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("FCB1010 Sound Player")
        self.central_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.central_widget)

        self.layout = QtWidgets.QVBoxLayout(self.central_widget)

        # --- MIDI入力デバイス選択 ---
        self.midi_input_label = QtWidgets.QLabel("MIDI Input Device:")
        self.layout.addWidget(self.midi_input_label)
        self.midi_input_combo = QtWidgets.QComboBox()
        self.midi_input_combo.addItems(get_midi_inputs())
        self.midi_input_combo.currentIndexChanged.connect(self.select_midi_input)
        self.layout.addWidget(self.midi_input_combo)

        # --- オーディオ出力デバイス選択 ---
        self.audio_output_label = QtWidgets.QLabel("Audio Output Device:")
        self.layout.addWidget(self.audio_output_label)
        self.audio_output_combo = QtWidgets.QComboBox()
        self.audio_output_combo.addItems(get_audio_outputs())
        self.audio_output_combo.currentIndexChanged.connect(self.select_audio_output)
        self.layout.addWidget(self.audio_output_combo)

        # --- ファイルリスト ---
        self.file_list_label = QtWidgets.QLabel("Files:")
        self.layout.addWidget(self.file_list_label)
        self.file_list_widget = QtWidgets.QListWidget()
        self.layout.addWidget(self.file_list_widget)
        self.file_list_widget.setMinimumHeight(200) # ファイルリストの最低限の高さ

        # --- MIDI入力レベル表示 ---
        self.midi_level_label = QtWidgets.QLabel("MIDI Input Level:")
        self.layout.addWidget(self.midi_level_label)
        self.midi_level_bar = QtWidgets.QProgressBar()
        self.midi_level_bar.setRange(0, 100)
        self.layout.addWidget(self.midi_level_bar)

        # --- オーディオ出力レベル表示 ---
        self.audio_level_label = QtWidgets.QLabel("Audio Output Level (pygame.mixer.get_busy()):")
        self.layout.addWidget(self.audio_level_label)
        self.audio_level_bar = QtWidgets.QProgressBar()
        self.audio_level_bar.setRange(0, 100)
        self.layout.addWidget(self.audio_level_bar)

        # --- タイマーで定期的にGUIを更新 ---
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_gui)
        self.timer.start(int(1000/REFRESH_RATE)) # REFRESH_RATEで指定した頻度(Hz)で更新

        # --- 初期化 ---
        self.select_midi_input(self.midi_input_combo.currentIndex())
        self.select_audio_output(self.audio_output_combo.currentIndex())
        self.update_file_list()

        # --- MIDIメッセージ処理スレッド開始 ---
        self.midi_thread = threading.Thread(target=process_midi_message, daemon=True)
        self.midi_thread.start()


    def select_midi_input(self, index):
        """MIDI入力デバイスが選択されたときの処理"""
        global selected_midi_input_index
        if selected_midi_input_index != index:
          close_midi_input()
          if open_midi_input(get_midi_inputs()[index]):
             selected_midi_input_index = index
             print(f"Selected MIDI input: {get_midi_inputs()[index]}")
          else:
              QtWidgets.QMessageBox.warning(self, "Error", "Failed to open selected MIDI input.")


    def select_audio_output(self, index):
        """オーディオ出力デバイスが選択されたときの処理"""
        global selected_audio_output_index
        selected_audio_output_index = index
        init_audio(index)
        print(f"Selected audio output: {audio_output_device}")


    def update_file_list(self):
        """ファイルリストを更新"""
        self.file_list_widget.clear()
        for i in range(1, 11):
            folder_path = str(i)
            if os.path.isdir(folder_path):
                files = [f for f in os.listdir(folder_path) if f.endswith(('.wav', '.mp3', '.ogg'))]
                for filename in files:
                    item = QtWidgets.QListWidgetItem(f"{i}: {filename}")
                    self.file_list_widget.addItem(item)

    def update_gui(self):
        """GUIを更新"""

        # Pygameのイベント処理
        process_pygame_events()

        # ファイルの再生状況を更新
        with lock:
            for i in range(self.file_list_widget.count()):
                item = self.file_list_widget.item(i)
                folder_num = int(item.text().split(':')[0])
                if folder_num in current_playing:
                    if current_playing[folder_num][0] == item.text().split(': ')[1]:
                      item.setBackground(QtGui.QColor(0, 255, 0))  # 緑色の背景
                    else:
                        item.setBackground(QtGui.QColor(255, 255, 255))  # 白色の背景
                else:
                    item.setBackground(QtGui.QColor(255, 255, 255))  # 白色の背景

        # MIDI入力レベルを更新
        self.midi_level_bar.setValue(int(midi_input_level * 100))

        # オーディオ出力レベルを更新 (アクティブなチャンネル数で代用)
        num_busy_channels = pygame.mixer.get_busy()
        self.audio_level_bar.setValue(int((num_busy_channels / 10) * 100)) # 10個のチャンネルを前提に正規化


    def closeEvent(self, event):
        """ウィンドウが閉じられたときの処理"""
        close_midi_input()
        pygame.quit()
        event.accept()

# --- メイン処理 ---

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
