# -*- coding: utf-8 -*-
import sys

sys.stdout.reconfigure(encoding='utf-8')

# The exact prompt text
original = """Cô trở về nhà. ◆ Buổi tối hôm đó, cô nhận được email thứ hai từ đúng cái địa chỉ vô danh đã gửi tệp cho cô lần trước. Lần này không có bất kỳ tệp đính kèm nào. Chỉ vỏn vẹn một dòng duy nhất: "Anh ta đã nộp báo cáo về 432Hz. Bản báo cáo sẽ bị xóa trước khi trời sáng. Cô có 4 tiếng." Thiên Ân đọc dòng chữ đó ba lần. 432Hz. Cô hoàn toàn không biết con số đó mang ý nghĩa gì. Nhưng kẻ gửi email thì biết rõ cô đang bám đuôi Hùng, biết tường tận những gì anh ta vừa làm, và biết trước cả số phận sắp sửa ập đến với thứ mà anh ta vừa nộp lên. Đó là ba dữ kiện mà một kẻ đứng ngoài tổ chức không đời nào có thể nắm bắt được. Cô ngồi bất động tại chỗ chừng 2 phút. Sau đó đứng phắt dậy, vơ lấy chiếc áo khoác, cầm theo máy ghi âm — tuyệt đối không dùng điện thoại, mà là một thiết bị ghi âm vật lý, dòng Sony cũ kỹ từ tận năm 2039 được cô mua lại ở một cửa tiệm đồ cũ, bởi lẽ những thiết bị kỹ thuật số hiện đại rất dễ bị hack — và lao thẳng ra ngoài. ◆ Trạm kiểm soát giếng khoan DN-31 lúc 23:00 tăm tối hơn cô hình dung. Zone 4 về đêm thưa thớt bóng người — chỉ lác đác vài công nhân ca đêm và những chuyến xe tải giao hàng tuân theo lịch trình nghiêm ngặt. Ánh đèn vàng vọt từ hệ thống chiếu sáng công nghiệp hắt xuống, cắt xẻ không gian thành những vệt bóng tối kéo dài lê thê giữa các khối nhà. Cô đứng yên ở phía bên kia đường, cách cổng chừng 20 mét, mắt dán vào màn hình chiếc máy tính bảng cầm trên tay — tạo ra một vỏ bọc hoàn hảo như thể một kẻ đang đứng đợi xe hay dò dẫm kiểm tra bản đồ. Không có bất kỳ động tĩnh nào đặc biệt xảy ra trong 20 phút đầu tiên. Rồi đột nhiên, ánh đèn trong phòng kỹ thuật — rọi ra từ dãy cửa sổ nhỏ bên hông tòa nhà — vụt tắt. Không hề đúng giờ — nó tắt sớm hơn hẳn so với lịch luân phiên ca kíp thông thường. Cô chăm chú quan sát. Chẳng thấy bóng người nào bước ra ngoài. 15 phút sau, cô nhận được email thứ ba từ cái địa chỉ vô danh quen thuộc: "Đã xóa. Anh ta không biết. Chưa." Cô đăm đăm nhìn màn hình điện thoại giữa bóng tối quánh đặc của đường phố. Trực tiếp trên đỉnh đầu, ánh đèn vàng công nghiệp hắt xuống, nhuốm lên mọi vật một sắc màu của sự cũ kỹ và mệt mỏi rệu rã. Bản báo cáo đã bị xóa. Một kẻ nào đó nằm vùng trong hệ thống, nắm giữ quyền truy cập đủ cao để có thể âm thầm thủ tiêu dữ liệu kỹ thuật ngay giữa đêm không, đã ra tay — và kẻ gửi email cho cô thậm chí đã tường tận mọi chuyện từ trước cả khi nó kịp xảy ra. Cô đứng chôn chân ở đó thêm một lúc, mặc cho luồng thông tin ấy từ từ ngấm vào nhận thức. Sau đó, cô bắt đầu sải bước đi bộ về nhà, bàn tay vẫn nắm chặt chiếc máy ghi âm nằm gọn trong túi áo khoác — thứ đồ vật mà đêm nay cô chẳng cần đụng tới, nhưng vẫn luôn mang theo như một thói quen cơ học — và miên man suy nghĩ. Phạm Trọng Hùng đã nộp một bản báo cáo về thứ gì đó mang tần số 432Hz. Bản báo cáo đó đã bị thủ tiêu ngay trong đêm. Và vào lúc 02:17 sáng, 4 ngày trước, một kẻ ẩn danh nào đó đã gửi cho cô bản danh sách bảy người làm việc tại cùng một chuỗi giếng khoan, tất cả đều đã bốc hơi hoặc bỏ mạng, với cái tên Phạm Trọng Hùng bị ghim bằng nhãn vàng giữa danh sách những người còn sống sót."""

beats = [
    "Cô trở về nhà. ◆ Buổi tối hôm đó, cô nhận được email thứ hai từ đúng cái địa chỉ vô danh đã gửi tệp cho cô lần trước. Lần này không có bất kỳ tệp đính kèm nào. ",
    'Chỉ vỏn vẹn một dòng duy nhất: "Anh ta đã nộp báo cáo về 432Hz. Bản báo cáo sẽ bị xóa trước khi trời sáng. Cô có 4 tiếng." Thiên Ân đọc dòng chữ đó ba lần. 432Hz. Cô hoàn toàn không biết con số đó mang ý nghĩa gì. ',
    "Nhưng kẻ gửi email thì biết rõ cô đang bám đuôi Hùng, biết tường tận những gì anh ta vừa làm, và biết trước cả số phận sắp sửa ập đến với thứ mà anh ta vừa nộp lên. ",
    "Đó là ba dữ kiện mà một kẻ đứng ngoài tổ chức không đời nào có thể nắm bắt được. Cô ngồi bất động tại chỗ chừng 2 phút. Sau đó đứng phắt dậy, vơ lấy chiếc áo khoác, cầm theo máy ghi âm — tuyệt đối không dùng điện thoại, mà là một thiết bị ghi âm vật lý, dòng Sony cũ kỹ từ tận năm 2039 được cô mua lại ở một cửa tiệm đồ cũ, bởi lẽ những thiết bị kỹ thuật số hiện đại rất dễ bị hack — và lao thẳng ra ngoài. ◆ ",
    "Trạm kiểm soát giếng khoan DN-31 lúc 23:00 tăm tối hơn cô hình dung. Zone 4 về đêm thưa thớt bóng người — chỉ lác đác vài công nhân ca đêm và những chuyến xe tải giao hàng tuân theo lịch trình nghiêm ngặt. ",
    "Ánh đèn vàng vọt từ hệ thống chiếu sáng công nghiệp hắt xuống, cắt xẻ không gian thành những vệt bóng tối kéo dài lê thê giữa các khối nhà. Cô đứng yên ở phía bên kia đường, cách cổng chừng 20 mét, mắt dán vào màn hình chiếc máy tính bảng cầm trên tay — tạo ra một vỏ bọc hoàn hảo như thể một kẻ đang đứng đợi xe hay dò dẫm kiểm tra bản đồ. ",
    "Không có bất kỳ động tĩnh nào đặc biệt xảy ra trong 20 phút đầu tiên. Rồi đột nhiên, ánh đèn trong phòng kỹ thuật — rọi ra từ dãy cửa sổ nhỏ bên hông tòa nhà — vụt tắt. Không hề đúng giờ — nó tắt sớm hơn hẳn so với lịch luân phiên ca kíp thông thường. Cô chăm chú quan sát. Chẳng thấy bóng người nào bước ra ngoài. ",
    '15 phút sau, cô nhận được email thứ ba từ cái địa chỉ vô danh quen thuộc: "Đã xóa. Anh ta không biết. Chưa." Cô đăm đăm nhìn màn hình điện thoại giữa bóng tối quánh đặc của đường phố. Trực tiếp trên đỉnh đầu, ánh đèn vàng công nghiệp hắt xuống, nhuốm lên mọi vật một sắc màu của sự cũ kỹ và mệt mỏi rệu rã. ',
    "Bản báo cáo đã bị xóa. Một kẻ nào đó nằm vùng trong hệ thống, nắm giữ quyền truy cập đủ cao để có thể âm thầm thủ tiêu dữ liệu kỹ thuật ngay giữa đêm không, đã ra tay — và kẻ gửi email cho cô thậm chí đã tường nhận mọi chuyện từ trước cả khi nó kịp xảy ra. ", # Wait, did I write 'tường nhận' or 'tường tận'? The original has 'tường tận'. Let's check my beat. I wrote 'tường nhận' here. I will fix it.
    "Cô đứng chôn chân ở đó thêm một lúc, mặc cho luồng thông tin ấy từ từ ngấm vào nhận thức. ",
    "Sau đó, cô bắt đầu sải bước đi bộ về nhà, bàn tay vẫn nắm chặt chiếc máy ghi âm nằm gọn trong túi áo khoác — thứ đồ vật mà đêm nay cô chẳng cần đụng tới, nhưng vẫn luôn mang theo như một thói quen cơ học — và miên man suy nghĩ. Phạm Trọng Hùng đã nộp một bản báo cáo về thứ gì đó mang tần số 432Hz. Bản báo cáo đó đã bị thủ tiêu ngay trong đêm. ",
    "Và vào lúc 02:17 sáng, 4 ngày trước, một kẻ ẩn danh nào đó đã gửi cho cô bản danh sách bảy người làm việc tại cùng một chuỗi giếng khoan, tất cả đều đã bốc hơi hoặc bỏ mạng, với cái tên Phạm Trọng Hùng bị ghim bằng nhãn vàng giữa danh sách những người còn sống sót."
]

# Let's fix 'tường nhận' to 'tường tận' in the list to match the original
beats[8] = beats[8].replace("tường nhận", "tường tận")

reconstructed = "".join(beats)
if reconstructed == original:
    print("Match: SUCCESS")
else:
    print("Match: FAILURE")
    print("Reconstructed len:", len(reconstructed))
    print("Original len:", len(original))
    for idx, (a, b) in enumerate(zip(reconstructed, original)):
        if a != b:
            print(f"Mismatch at index {idx}: reconstructed={repr(reconstructed[idx:idx+20])}, original={repr(original[idx:idx+20])}")
            break
