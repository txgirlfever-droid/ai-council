# AI Council — đọc nhanh trong 2 phút

**Một câu:** bạn hỏi trên Telegram, **3 AI của 3 hãng** bàn bạc với nhau, rồi gửi bạn
bản tóm tắt để **bạn quyết định**.

## Sơ đồ

```mermaid
flowchart TD
    A["1. Bạn hỏi trên Telegram"]
    B["2. Bot ghi lại câu hỏi"]
    subgraph C["3. Ba AI bàn bạc"]
        direction LR
        C1["Codex<br/>điều phối"]
        C2["Claude<br/>phản biện"]
        C3["Gemini<br/>tìm phương án"]
    end
    P["Thiếu AI trả lời<br/>→ tạm dừng, báo bạn"]
    D["4. Gửi bạn bản tóm tắt"]
    E["5. Bạn bấm Duyệt / Từ chối / Hoãn"]
    F["6. Lưu vĩnh viễn"]

    A --> B --> C --> D --> E --> F
    C -. nếu thiếu .-> P

    classDef council fill:#EEEDFE,stroke:#534AB7,color:#3C3489
    classDef warn fill:#FAEEDA,stroke:#854F0B,color:#633806
    class C,C1,C2,C3 council
    class P warn
```

## Ba AI bàn bạc thế nào?

**Tự nghĩ → Góp ý cho nhau → Tranh luận (tối đa 3 vòng) → Tóm tắt.**
Ý kiến phản đối luôn được giữ lại, không bị giấu.

## Dùng thế nào?

| Gõ | Để làm gì |
|---|---|
| `/council câu hỏi` | Hỏi hội đồng |
| `/council_critical câu hỏi` | Câu quan trọng: cả 3 AI phải trả lời |
| `/status` | Xem các cuộc họp đang mở |
| `/help` | Xem hướng dẫn |

## An toàn

- ✅ Chỉ bạn ra lệnh được.
- ✅ Không bịa kết quả: thiếu AI trả lời thì tạm dừng và báo bạn.
- ✅ Có trần chi phí mỗi lần hỏi (mặc định 5 USD).
- ✅ Quyết định đã bấm không sửa, không xóa được.

## Cần chuẩn bị (một lần)

1. Bot Telegram (tạo qua **@BotFather**).
2. Khóa API của ít nhất **2** hãng AI.
3. Máy chủ chạy 24/7 có **PostgreSQL**.
4. Điền vào file `.env`, rồi làm theo **Quick Start** trong [README](../README.md).

**Đổi sang model AI mới?** Chỉ cần sửa một dòng trong `.env`, không phải sửa code.
