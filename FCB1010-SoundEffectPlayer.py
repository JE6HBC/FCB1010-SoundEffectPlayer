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
DEFAULT_VOLUME = 1.0 # デフォルトの音量 (最大)

# --- グローバル変数 ---
midi_in = None
audio_output_device = None
selected_midi_input_index = -1
selected_audio_output_index = -1
current_playing = {}  # {switch_number: (filename, channel, sound)}  # soundオブジェクトも保持
queued_sounds = {} # {switch_number: filename}  # 再生待ちのサウンド
volume = DEFAULT_VOLUME
midi_input_level = 0
audio_output_level = 0
lock = threading.Lock()  # スレッド間の排他制御用
use_midi_volume = True # MIDIコントロールを優先するかどうか

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
    pygame.init()  # pygame全体の初期化
    device_names = []

    try:
        num_devices = pygame.mixer.get_num_channels()
    except pygame.error:
        print("Error: Pygame mixer could not be initialized. Audio output may not work.")
        return []

    for i in range(num_devices):
        try:
            # 各デバイスに対してmixerを初期化を試みる
            pygame.mixer.init(44100, -16, 2, 512, devicename=str(i))
            device_names.append(str(i))  # デバイス番号を文字列として追加
            pygame.mixer.quit()  # 一度閉じる
        except pygame.error:
            # 初期化に失敗しても処理を続ける
            pass

    # main loopでの利用のため、再度初期化
    pygame.mixer.init()

    return device_names


def init_audio(device_index=None):
    """オーディオを初期化"""
    global audio_output_device
    pygame.init()

    if device_index is not None:
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
        channel.set_endevent() # チャンネルごとに固有のイベントタイプを自動割り当て

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
    for _, (_, channel, _) in current_playing.items(): # soundオブジェクトは使用しない
        if channel:
            channel.set_volume(volume)

# --- MIDI/キーボード入力処理 ---
def process_midi_message():
    """MIDIメッセージを処理するスレッド"""
    global midi_input_level, current_playing, audio_output_level, queued_sounds
    global use_midi_volume, volume

    if not midi_in:
      return
    
    while True: # 無限ループでMIDIメッセージを監視
      for msg in midi_in.read(128): # バッファにあるメッセージを全て読み出す
        if msg.type == 'control_change':
            midi_input_level = msg.value / 127.0  # MIDIレベルを更新

            if msg.control >= 1 and msg.control <= 10:  # スイッチ1-10
                switch_number = msg.control
                process_input(switch_number, msg.value) # MIDIとキーボードで共通の処理

            elif msg.control == 7:  # エクスプレッションペダル (CC#7)
                use_midi_volume = True # MIDIボリュームコントロールを有効にする
                set_volume(msg.value / 127.0)
      time.sleep(0.001)  # CPU負荷を下げるために少し待機


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
        if not files:
            return

        with lock:  # 排他制御を開始
            if value > 0:  # スイッチ/キーが押された
                filename = random.choice(files)
                if switch_number in current_playing:
                    # 既に再生中の場合はキューに追加
                    queued_sounds[switch_number] = filename
                else:
                    # 再生中でなければ即座に再生
                    full_path = os.path.join(folder_path, filename)
                    sound, channel = play_sound(full_path, switch_number - 1)
                    if sound and channel:
                        current_playing[switch_number] = (filename, channel, sound) #soundも保持
            else:  # スイッチ/キーが離された
                if switch_number in current_playing:
                    _, channel, _ = current_playing[switch_number] # soundオブジェクトは使用しない
                    if channel:
                         channel.fadeout(500)
                    # キューには追加/削除しない（再生終了時に処理）

# --- Pygameイベント処理 ---
def process_pygame_events():
    """Pygameのイベント(音声再生終了、キーボード入力)を処理する"""
    global current_playing, queued_sounds

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()

        if event.type == pygame.USEREVENT: # チャンネル終了イベント
             with lock:
                # 終了したチャンネルを探す
                finished_channel_number = None
                for switch_num, (_, channel, _) in current_playing.items():
                    if not channel.get_busy(): # 再生終了を検知
                        finished_channel_number = switch_num
                        break

                if finished_channel_number is not None:
                    del current_playing[finished_channel_number]  # 再生終了したエントリを削除

                    # キューに次のサウンドがあれば再生
                    if finished_channel_number in queued_sounds:
                        filename = queued_sounds.pop(finished_channel_number)
                        folder_path = str(finished_channel_number)
                        full_path = os.path.join(folder_path, filename)
                        sound, channel = play_sound(full_path, finished_channel_number - 1)
                        if sound and channel:
                            current_playing[finished_channel_number] = (filename, channel, sound)


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


        # --- ボリュームコントロール ---
        self.volume_label = QtWidgets.QLabel("Volume:")
        self.layout.addWidget(self.volume_label)
        self.volume_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.volume_slider.setRange(0, 100)  # 0-100の範囲
        self.volume_slider.setValue(int(DEFAULT_VOLUME * 100))  # 初期値を設定
        self.volume_slider.valueChanged.connect(self.slider_volume_changed)
        self.layout.addWidget(self.volume_slider)


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

    def slider_volume_changed(self, value):
        """スライダーの値が変更されたときの処理"""
        global use_midi_volume
        if not use_midi_volume: # MIDIコントロルが無効な場合のみ
            set_volume(value / 100.0)

    def select_midi_input(self, index):
        """MIDI入力デバイスが選択されたときの処理"""
        global selected_midi_input_index, use_midi_volume
        if selected_midi_input_index != index:
          close_midi_input()
          if open_midi_input(get_midi_inputs()[index]):
             selected_midi_input_index = index
             print(f"Selected MIDI input: {get_midi_inputs()[index]}")
             use_midi_volume = True # MIDI入力を開いたらMIDIボリュームを有効化
          else:
              QtWidgets.QMessageBox.warning(self, "Error", "Failed to open selected MIDI input.")
              use_midi_volume = False # MIDI入力が失敗したらMIDIボリュームを無効化


    def select_audio_output(self, index):
        """オーディオ出力デバイスが選択されたときの処理"""
        global selected_audio_output_index, audio_output_device
        selected_audio_output_index = index

        # 利用可能なオーディオ出力デバイスのリストを取得 (get_audio_outputs()を再呼び出し)
        available_devices = get_audio_outputs()
        if not available_devices:  # デバイスが1つも見つからない場合
            QtWidgets.QMessageBox.warning(self, "Error", "No audio output devices found.")
            return

        if index < len(available_devices):  # 選択されたインデックスが有効範囲内なら
            try:
                #選択されたデバイスで初期化
                init_audio(int(available_devices[index]))  # 文字列を整数に変換
                print(f"Selected audio output: Device {available_devices[index]}") # 選択されたデバイス番号を表示
            except pygame.error as message:
                QtWidgets.QMessageBox.warning(self, "Error", f"Cannot initialize audio device: {message}")
        else:  # indexが範囲外の場合(通常は起こらないはず)
            QtWidgets.QMessageBox.warning(self, "Error", "Invalid audio output device selected.")
            init_audio()  # デフォルトデバイスで初期化を試みる


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
        global use_midi_volume  # use_midi_volume がグローバル変数であることを宣言

        # Pygameのイベント処理
        process_pygame_events()

        # ファイルの再生状況を更新(文字色を反転)
        with lock:
            for i in range(self.file_list_widget.count()):
                item = self.file_list_widget.item(i)
                folder_num = int(item.text().split(':')[0])
                if folder_num in current_playing:
                    if current_playing[folder_num][0] == item.text().split(': ')[1]:
                        item.setBackground(QtGui.QColor(0, 255, 0))  # 緑色の背景
                        # 反転色を取得
                        inverted_color = item.background().color().rgb() ^ 0xFFFFFF
                        item.setForeground(QtGui.QColor(inverted_color))

                    else:
                        item.setBackground(QtGui.QColor(255, 255, 255))  # 白色の背景
                        item.setForeground(QtGui.QColor(0, 0, 0)) # 黒色の文字
                else:
                    item.setBackground(QtGui.QColor(255, 255, 255))  # 白色の背景
                    item.setForeground(QtGui.QColor(0, 0, 0)) # 黒色の文字

        # MIDI入力レベルを更新
        self.midi_level_bar.setValue(int(midi_input_level * 100))

        # オーディオ出力レベルを更新 (アクティブなチャンネル数で代用)
        num_busy_channels = pygame.mixer.get_busy()
        self.audio_level_bar.setValue(int((num_busy_channels / 10) * 100)) # 10個のチャンネルを前提に正規化

        # ボリュームスライダーの位置を更新 (MIDIコントロールが有効な場合は更新しない)
        if not use_midi_volume:
            self.volume_slider.setValue(int(volume * 100))
        else: # MIDIコントロールが有効なときは、use_midi_volumeをFalseにしてスライダーと同期
            use_midi_volume = False


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
