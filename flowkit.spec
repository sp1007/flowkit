# -*- mode: python ; coding: utf-8 -*-
"""Đóng gói Flow Kit Next thành .exe.

Đóng gói kiểu MỘT THƯ MỤC (`onedir`), không phải một file. Bản một-file phải giải nén
toàn bộ vào thư mục tạm mỗi lần chạy — chậm hơn hẳn khi khởi động, và một số phần mềm
diệt virus soi kỹ file exe tự giải nén. Ở đây không có nhu cầu mang đi một file, nên
onedir tốt hơn.

CHỖ DỄ SAI NHẤT: dữ liệu người dùng (agent/studio.db, media/, webapp/dist) KHÔNG được
gói vào exe. Chúng nằm cạnh exe và được `run_server.py` trỏ tới bằng biến môi trường.
Gói vào thì mỗi lần build là một bản sao 14,5 GB, và dữ liệu mới sinh ra sẽ nằm trong
thư mục tạm rồi biến mất khi thoát.

Chỉ gói các file CẤU HÌNH đi cùng code, vì chúng được đọc qua `__file__` của module.

Build:  pyinstaller flowkit.spec --noconfirm
Kết quả: dist/FlowKitNext/FlowKitNext.exe  (chép cả thư mục sang chỗ cài đặt)
"""

datas = [
    ("agent/models.json", "agent"),
    ("agent/boq_model_catalog.json", "agent"),
    ("agent/boq_rpcids.json", "agent"),
    # brain.py đọc presets/character-sheet-prompt.txt qua `__file__`, tức từ thư mục giải
    # nén. Thiếu là app VẪN CHẠY nhưng lặng lẽ rơi về mẫu sheet nhân vật cũ — sai lệch chỉ
    # lộ ra ở chất lượng ảnh sinh ra, nên rất khó lần.
    ("presets", "presets"),
]

hiddenimports = [
    # uvicorn nạp các lớp này bằng TÊN lúc chạy, nên PyInstaller không thấy chúng khi
    # quét import tĩnh — thiếu là exe chết ngay lúc khởi động server.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    # Các router chỉ được nạp qua chuỗi/động ở vài chỗ.
    "agent.api.flow",
    "agent.api.flow_v2",
    "agent.api.studio",
    "agent.api.music",
    "agent.api.tts",
    "agent.api.ai_agent",
    "agent.services.boq_client",
    "agent.services.boq_compat",
    "agent.services.boq_ops",
    "agent.services.boq_prices",
]

a = Analysis(
    ["run_server.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Bỏ những thứ nặng mà server không dùng. Không loại thì exe phình lên vô ích vì
    # chúng bị kéo theo qua các thư viện phụ.
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide2", "notebook", "IPython"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FlowKitNext",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # giữ cửa sổ console: đóng nó là tắt server, và log lỗi hiện ngay
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FlowKitNext",
)
