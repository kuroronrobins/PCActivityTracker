#!/usr/bin/env python
"""
shutdown.py - このスクリプトはPCのシャットダウン／中断時に実行され、
既存の起動レコードに対して shutdown_time および duration を更新します。
※ 起動時に記録された user_account は上書きせず、既存の値を保持します。
"""

import os
import sys
import socket
import datetime
import logging

# グループポリシー経由で実行される際、カレントディレクトリが不定になるため、スクリプトのあるディレクトリに変更
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(base_dir)

# ログファイルの絶対パスを設定（EXE と同じディレクトリ内に "application.log" を作成）
log_path = os.path.join(base_dir, "application.log")
logging.basicConfig(
    level=logging.INFO,  # 初期値は INFO だが、後ほど debug 値により変更する
    format='%(asctime)s [%(levelname)s] %(message)s',
    filename=log_path,
    filemode='a'
)

# 共通のユーティリティ関数を utils.py からインポート
from utils import (
    load_config,
    ensure_table_exists,
    wait_for_network_share,
    get_start_time_for_duration,
    compute_duration,
    update_shutdown_info_with_retry,
)

def get_shutdown_info():
    """
    シャットダウン／中断時に必要な情報を収集し、辞書形式で返す。

    取得項目:
      - pc_id       : ホスト名（PC固有ID）
      - user_account: 初期値は環境変数 "USERNAME" から取得するが、後で起動レコードの値に置き換え
      - shutdown_time: 現在のシャットダウン時刻（YYYY-MM-DD HH:MM:SS形式）
      - weekday     : シャットダウン時の曜日（例："Mon"）
      - session_type: "normal"（初期値）
      - duration    : 0（初期値、後で算出）
    """
    data = {}
    data['pc_id'] = socket.gethostname()
    data['user_account'] = os.environ.get("USERNAME", "unknown")
    now = datetime.datetime.now()
    data['shutdown_time'] = now.strftime("%Y-%m-%d %H:%M:%S")
    data['weekday'] = now.strftime("%a")
    data['session_type'] = "normal"
    data['duration'] = 0
    return data

def main():
    # 設定ファイル (config.ini) をロード
    config = load_config("config.ini")
    # config.ini の debug の値によりログレベルを変更（Trueなら DEBUG, Falseなら INFO）
    debug = config.getboolean("General", "debug", fallback=False)
    new_level = logging.DEBUG if debug else logging.INFO
    logging.getLogger().setLevel(new_level)

    db_path = config.get("Database", "db_path")
    max_retries = config.getint("Retry", "max_retries")
    retry_interval = config.getfloat("Retry", "retry_interval")
    timeout = config.getfloat("General", "timeout")

    # ネットワーク共有上のDBファイルが利用可能になるまで待機
    wait_for_network_share(db_path)

    # テーブルが存在しない場合は自動で作成する
    ensure_table_exists(db_path, timeout)

    # シャットダウン時の情報を収集
    shutdown_info = get_shutdown_info()

    # 最新の起動レコードから start_time と user_account を取得
    result = get_start_time_for_duration(db_path, shutdown_info['pc_id'], shutdown_info['user_account'], timeout)
    if result:
        start_time_str, db_user_account = result
        shutdown_info['duration'] = compute_duration(start_time_str, shutdown_info['shutdown_time'])
        # 起動時に記録された user_account を保持する
        shutdown_info['user_account'] = db_user_account
    else:
        shutdown_info['duration'] = 0

    logging.info("Shutdown info: %s", shutdown_info)

    # 再試行ロジック付きで、DBの起動レコードをシャットダウン情報で更新または新規挿入する
    update_shutdown_info_with_retry(db_path, shutdown_info, max_retries, retry_interval, timeout)
    logging.info("Shutdown record updated successfully.")

if __name__ == "__main__":
    main()
