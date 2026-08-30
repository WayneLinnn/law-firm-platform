# -*- coding: utf-8 -*-
"""Step 11 · 案卷文档。

重点验四件事：

  1. **权限跟着案件走** —— 看不到案子的人，一个字节都拿不到
  2. **版本只增不改** —— 「我们当时递上去的是哪一版」必须答得出
  3. **删除是软删** —— 谁在什么时候删了什么，合规要问
  4. ★ **搜不到的文件要说清楚** —— 扫描件不是"案卷里没有"
"""
from __future__ import annotations

import io as _io
from datetime import date

import pytest

from kernel.container import container
from kernel.errors import DomainError, NotFound, PermissionDenied
from modules.case import service as case_svc
from modules.crm import service as crm
from modules.doc import extract as ex
from modules.doc import service
from modules.identity import access
from modules.identity import service as identity


@pytest.fixture
def session():
    """每个模块的测试各自建 session —— 项目里就是这么约定的。"""
    from sqlalchemy.orm import Session as SASession

    from kernel.db.session import get_engine

    try:
        conn = get_engine().connect()
    except Exception as exc:                       # noqa: BLE001
        pytest.skip("数据库不可用：{0}".format(exc))
    tx = conn.begin()
    s = SASession(bind=conn, expire_on_commit=False)
    try:
        yield s
    finally:
        s.close()
        tx.rollback()
        conn.close()


@pytest.fixture(autouse=True)
def memory_storage():
    """用内存存储跑测试 —— 单测不该依赖 MinIO 起没起。"""
    from adapters.storage.memory import MemoryStorage

    store = MemoryStorage()
    container.bind("storage", lambda: store)
    yield store
    from kernel.bootstrap import bind_adapters

    bind_adapters()


@pytest.fixture
def firm(session):
    import uuid

    sfx = uuid.uuid4().hex[:8]
    t = identity.create_tenant(session, "doc-" + sfx, "康桥")
    identity.ensure_default_positions(session, t.id)
    access.ensure_default_roles(session, t.id)
    causes = {c.code: c for c in case_svc.ensure_default_causes(session, t.id)}

    root = identity.create_org_unit(session, t.id, "康桥", "r-" + sfx, kind="firm")
    t1 = identity.create_org_unit(session, t.id, "王团队", "t1-" + sfx,
                                  kind="team", parent_id=root.id)
    t2 = identity.create_org_unit(session, t.id, "李团队", "t2-" + sfx,
                                  kind="team", parent_id=root.id)

    def person(name, org, role):
        u = identity.create_user(session, t.id, name + sfx, name, "password123")
        identity.appoint(session, u.id, "senior_partner")
        identity.assign_membership(session, u.id, org.id, is_primary=True)
        access.assign_role(session, u.id, role)
        return u

    wang = person("wang", t1, "committee")      # 全所
    zhang = person("zhang", t2, "lawyer")       # 只看自己

    party = crm.create_party(session, t.id, "宏远建设集团有限公司", allow_similar=True)
    case = case_svc.create_case(
        session, t.id, "AJ" + sfx[:6], "宏远诉凯达",
        lead_id=wang.id, cause_id=causes["construction"].id, org_unit_id=t1.id,
    )
    case_svc.add_party(session, t.id, case.id, party.id, role="plaintiff", is_ours=True)

    return {
        "tenant": t, "case": case, "wang": wang, "zhang": zhang,
        "p_wang": access.build_principal(session, wang.id),
        "p_zhang": access.build_principal(session, zhang.id),
    }


def _upload(session, firm, name="起诉状.txt", body="本案工程款优先受偿权应予支持", **kw):
    return service.upload(
        session, firm["p_wang"], firm["case"].id,
        filename=name, data=body.encode("utf-8"),
        content_type="text/plain", **kw)


# ================================================================ 上传与版本


def test_upload_creates_document_and_first_version(session, firm):
    doc, ver = _upload(session, firm, title="起诉状", folder="诉讼文书")
    assert doc.title == "起诉状" and doc.folder == "诉讼文书"
    assert doc.current_version == 1 and ver.version == 1
    assert ver.sha256 and ver.size > 0
    assert ver.text_status == "ok" and "优先受偿权" in ver.text


def test_new_upload_adds_a_version_and_keeps_the_old_one(session, firm):
    """★ 版本只增不改 —— 旧版本必须原样留着。"""
    doc, v1 = _upload(session, firm, title="起诉状", body="第一稿")
    doc, v2 = service.upload(
        session, firm["p_wang"], firm["case"].id,
        filename="起诉状.txt", data="第二稿，补充了优先受偿权".encode("utf-8"),
        content_type="text/plain", document_id=doc.id)

    assert doc.current_version == 2 and v2.version == 2
    versions = service.versions_of(session, doc.id)
    assert [v.version for v in versions] == [2, 1]
    old = [v for v in versions if v.version == 1][0]
    assert old.text == "第一稿", "旧版本被改了 —— 「当时递的是哪一版」就答不出了"
    assert old.storage_key != v2.storage_key, "两个版本不能指向同一个对象"


def test_identical_file_does_not_create_a_version(session, firm):
    """同一个文件重复上传不该白占一个版本号 —— 律师常会重复点。"""
    doc, _ = _upload(session, firm, body="一模一样")
    with pytest.raises(DomainError) as e:
        service.upload(session, firm["p_wang"], firm["case"].id,
                       filename="x.txt", data="一模一样".encode("utf-8"),
                       content_type="text/plain", document_id=doc.id)
    assert "完全相同" in str(e.value)


def test_empty_and_oversize_are_refused(session, firm):
    with pytest.raises(DomainError):
        service.upload(session, firm["p_wang"], firm["case"].id,
                       filename="empty.txt", data=b"")
    with pytest.raises(DomainError) as e:
        service.upload(session, firm["p_wang"], firm["case"].id,
                       filename="big.bin", data=b"x" * (service.MAX_UPLOAD + 1))
    assert "上限" in str(e.value)


def test_storage_key_is_ascii_only(session, firm):
    """★ 键里不能带中文/空格 —— 各家对象存储的转义规则不一样，踩了回不来。"""
    doc, ver = _upload(session, firm, name="起 诉 状（终稿）.txt")
    assert ver.storage_key.isascii(), ver.storage_key
    assert ver.filename == "起 诉 状（终稿）.txt", "真实文件名要留着"


def test_file_body_really_lands_in_storage(session, memory_storage, firm):
    doc, ver = _upload(session, firm, body="正文内容")
    _, _, data = service.download(session, firm["p_wang"], ver.id)
    assert data.decode("utf-8") == "正文内容"


# ================================================================ 权限


def test_document_inherits_case_visibility(session, firm):
    """★ 张三看不到这个案子，就不该看到案卷里的任何东西。"""
    doc, ver = _upload(session, firm)

    with pytest.raises((PermissionDenied, NotFound)):
        service.get_document(session, firm["p_zhang"], doc.id)
    with pytest.raises((PermissionDenied, NotFound)):
        service.download(session, firm["p_zhang"], ver.id)
    with pytest.raises((PermissionDenied, NotFound)):
        service.list_documents(session, firm["p_zhang"], firm["case"].id)


def test_search_never_leaks_across_scope(session, firm):
    """★ 全文检索是最容易漏权限的地方：正文里什么都有。"""
    _upload(session, firm, body="这段话里有优先受偿权三个字")
    mine = service.search(session, firm["p_wang"], "优先受偿权")
    assert mine and mine[0]["title"]

    theirs = service.search(session, firm["p_zhang"], "优先受偿权")
    assert theirs == [], "张三从案卷正文里读到了他看不到的案子"


def test_upload_to_invisible_case_is_refused(session, firm):
    with pytest.raises((PermissionDenied, NotFound)):
        service.upload(session, firm["p_zhang"], firm["case"].id,
                       filename="x.txt", data=b"hi")


# ================================================================ 全文检索


def test_search_finds_by_body_and_title(session, firm):
    _upload(session, firm, title="答辩状", body="发包人擅自使用未经验收的工程")
    assert service.search(session, firm["p_wang"], "擅自使用")
    assert service.search(session, firm["p_wang"], "答辩状")


def test_search_returns_a_snippet_around_the_hit(session, firm):
    _upload(session, firm, body="前面一堆废话" * 20 + "关键在于优先受偿权" + "后面一堆废话" * 20)
    hit = service.search(session, firm["p_wang"], "优先受偿权")[0]
    assert "优先受偿权" in hit["snippet"]
    assert len(hit["snippet"]) < 200, "片段应当是截出来的，不是整篇"


def test_search_only_looks_at_the_current_version(session, firm):
    """搜到的应该是现在的内容，不是被改掉的旧稿。"""
    doc, _ = _upload(session, firm, body="旧稿写的是甲说法")
    service.upload(session, firm["p_wang"], firm["case"].id,
                   filename="x.txt", data="新稿改成了乙说法".encode("utf-8"),
                   content_type="text/plain", document_id=doc.id)
    assert service.search(session, firm["p_wang"], "乙说法")
    assert not service.search(session, firm["p_wang"], "甲说法")


def test_one_character_keyword_is_refused(session, firm):
    with pytest.raises(DomainError):
        service.search(session, firm["p_wang"], "工")


def test_deleted_documents_drop_out_of_search(session, firm):
    doc, _ = _upload(session, firm, body="这里提到工程价款")
    assert service.search(session, firm["p_wang"], "工程价款")
    service.soft_delete(session, firm["p_wang"], doc.id)
    assert not service.search(session, firm["p_wang"], "工程价款")


# ================================================================ ★ 搜不到的文件


def test_scanned_pdf_is_reported_not_silently_empty():
    """★★ 这条是本模块最要紧的一条。

    律所案卷里全是扫描件。如果它被静默记成「正文为空」，律师搜不到时
    会以为**案卷里没有这份材料** —— 但它明明躺在那儿。
    搜不到比搜错更危险，因为搜不到不会引起怀疑。
    """
    # ★ 用 pypdf 生成一个**合法但没有文字层**的 PDF —— 这正是扫描件的样子。
    #   手搓一段残缺 PDF 是不行的：那会被判成 failed（打不开），
    #   而真实扫描件是打得开的合法文件，只是里面只有图没有字。
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = _io.BytesIO()
    writer.write(buf)

    text, status, note = ex.extract(buf.getvalue(), "扫描件.pdf", "application/pdf")
    assert text == ""
    assert status == "empty", "扫描件被记成了别的状态"
    assert "扫描件" in note, "没告诉律师这是扫描件，他会以为案卷里没有"
    assert "OCR" in note


def test_images_are_marked_unsupported_with_a_reason():
    text, status, note = ex.extract(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, "证据照片.png")
    assert status == "unsupported" and "图片" in note


def test_extraction_never_raises_even_on_garbage():
    """抽取失败不该挡住上传 —— 文件本体已经存好了，抽正文只是为了检索。"""
    for data, name in [(b"\x00\x01\x02\x03", "坏文件.pdf"),
                       (b"not really a docx", "假的.docx"),
                       (b"", "空的.txt")]:
        text, status, note = ex.extract(data, name)
        assert status in ("ok", "empty", "unsupported", "failed")


def test_unsearchable_list_tells_the_lawyer_what_he_cannot_find(session, firm):
    """★ 把搜不到的文件单独列出来 —— 否则律师会做出错误判断。"""
    _upload(session, firm, title="能搜到的", body="这份有正文")
    service.upload(session, firm["p_wang"], firm["case"].id,
                   filename="扫描的判决书.png", data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 40,
                   content_type="image/png", title="扫描的判决书")

    blind = service.unsearchable(session, firm["p_wang"], firm["case"].id)
    assert len(blind) == 1
    assert blind[0]["title"] == "扫描的判决书"
    assert blind[0]["reason"], "只说搜不到、不说为什么，等于没说"

    st = service.stats(session, firm["p_wang"], firm["case"].id)
    assert st["documents"] == 2 and st["unsearchable"] == 1


# ================================================================ 删除


def test_delete_is_soft_and_recorded(session, firm):
    """★ 案卷不做物理删除 —— 谁在什么时候删了什么，合规要问。"""
    doc, ver = _upload(session, firm)
    service.soft_delete(session, firm["p_wang"], doc.id)

    assert doc.deleted and doc.deleted_by == firm["wang"].id and doc.deleted_at
    assert doc.id not in [d.id for d in
                          service.list_documents(session, firm["p_wang"], firm["case"].id)]
    assert doc.id in [d.id for d in service.list_documents(
        session, firm["p_wang"], firm["case"].id, include_deleted=True)]

    # 文件本体还在，还下得动
    _, _, data = service.download(session, firm["p_wang"], ver.id)
    assert data


def test_deleted_can_be_restored(session, firm):
    doc, _ = _upload(session, firm)
    service.soft_delete(session, firm["p_wang"], doc.id)
    service.restore(session, firm["p_wang"], doc.id)
    assert not doc.deleted


def test_double_delete_is_refused(session, firm):
    doc, _ = _upload(session, firm)
    service.soft_delete(session, firm["p_wang"], doc.id)
    with pytest.raises(DomainError):
        service.soft_delete(session, firm["p_wang"], doc.id)


# ================================================================ 存文字


def test_save_text_lands_as_a_document(session, firm):
    """流程产出的意见书就走这条 —— 不能只活在内存里。"""
    doc, ver = service.save_text(
        session, firm["p_wang"], firm["case"].id,
        title="法律意见书（恒和项目）",
        text="【结论】可以主张工程款。\n【理由】未经验收擅自使用…")
    assert doc.folder == "法律意见"
    assert ver.filename.endswith(".md") and ver.text_status == "ok"
    assert service.search(session, firm["p_wang"], "擅自使用")


def test_save_empty_text_is_refused(session, firm):
    with pytest.raises(DomainError):
        service.save_text(session, firm["p_wang"], firm["case"].id, "空的", "   ")


# ================================================================ 改


def test_rename_and_move_between_folders(session, firm):
    doc, _ = _upload(session, firm, title="临时名", folder="其他")
    service.rename(session, firm["p_wang"], doc.id,
                   title="民事起诉状", folder="诉讼文书", note="已递交")
    assert doc.title == "民事起诉状" and doc.folder == "诉讼文书" and doc.note == "已递交"


def test_default_folders_cover_a_real_case_file():
    fs = service.folders()
    for want in ("诉讼文书", "证据材料", "合同协议", "法律意见"):
        assert want in fs
