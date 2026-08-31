"""事件抽取的时间换算、条目切分与类目规则测试。规则见 docs/event-driven-plan.md 第 3、4 节。"""
from __future__ import annotations

import unittest

from events.event_extract import classify, split_items
from events.wechat_time import title_date, to_beijing, verify_offset


class WechatTimeTest(unittest.TestCase):
    def test_export_time_is_utc_minus_5(self):
        bj = to_beijing("2023-04-26 19:45:41")
        self.assertEqual(bj.strftime("%Y-%m-%d %H:%M"), "2023-04-27 08:45")

    def test_offset_is_fixed_across_dst(self):
        # 导出机器已关闭夏令时，冬夏两季都是 +13 小时
        self.assertEqual(to_beijing("2026-01-15 19:00:00").strftime("%m-%d %H"), "01-16 08")
        self.assertEqual(to_beijing("2026-07-15 19:00:00").strftime("%m-%d %H"), "07-16 08")

    def test_title_date_forms(self):
        self.assertEqual(title_date("2023.4.27早报 国际要闻"), (4, 27))
        self.assertEqual(title_date("【12月05日早报】国际新闻"), (12, 5))
        self.assertEqual(title_date("【26年8月24日早报】"), (8, 24))
        self.assertIsNone(title_date("离岸人民币隔夜下跌"))

    def test_verify_offset_rejects_wrong_offset(self):
        # 标题日期与换算结果差一天，匹配率为 0，必须抛错而不是静默通过
        rows = [{"time": "2023-04-26 19:45:41", "content": "2023.4.26早报 x"} for _ in range(120)]
        with self.assertRaises(RuntimeError):
            verify_offset(rows)

    def test_verify_offset_passes(self):
        rows = [{"time": "2023-04-26 19:45:41", "content": "2023.4.27早报 x"} for _ in range(120)]
        self.assertEqual(verify_offset(rows)["rate"], 1.0)


class SplitItemsTest(unittest.TestCase):
    def test_sections_and_bullets(self):
        content = (
            "2023.4.27早报\n国际要闻\n"
            "*美国众议院表决通过债务上限提案，白宫表示不可能成为法律；\n"
            "*欧洲央行副行长表示欧元区经济看似能避免衰退；\n"
            "国内要闻\n"
            "*中国央行开展逆回购操作，规模为一万亿元人民币；\n"
        )
        items = split_items(content)
        self.assertEqual([s for s, _ in items], ["国际要闻", "国际要闻", "国内要闻"])
        self.assertTrue(items[0][1].startswith("美国众议院"))

    def test_numbered_bullets(self):
        items = split_items("【国内新闻】：（1）央行行长表示货币政策保持正常区间。（2）商务部副部长谈开放。")
        self.assertEqual(len(items), 1)  # 同一行内的编号不切分，整行作为一条

    def test_non_zaobao_message_is_single_item(self):
        items = split_items("周四纽约尾盘，美元指数跌0.01%报96.2092，市场等待G20会议结果。")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][0], "single")

    def test_short_fragment_dropped(self):
        self.assertEqual(split_items("*好的\n"), [])


class ClassifyTest(unittest.TestCase):
    def test_us_monetary(self):
        self.assertEqual(classify("美联储官员称下周降息25基点已形成共识，点阵图显示明年还有两次"), "US_MON")

    def test_us_data(self):
        self.assertEqual(classify("美国上周首次申领失业金人数创逾三年新低，非农就业超预期"), "US_DATA")

    def test_cn_policy(self):
        self.assertEqual(classify("中国央行将于12月5日开展1万亿元买断式逆回购操作，期限为3个月"), "CN_POL")

    def test_cn_data(self):
        self.assertEqual(classify("中国7月社融数据低于预期，进出口同比增速回落，统计局称物价温和"), "CN_DATA")

    def test_trade_beats_country_rules(self):
        # 贸易类目优先级高于美国货币政策，避免"关税影响联储"被误分
        self.assertEqual(classify("华府对加拿大商品征收50%关税，加方宣布对等反制"), "TRADE")

    def test_fx_policy_highest_priority(self):
        self.assertEqual(classify("央行上调外汇存款准备金率，人民币中间价连续调升"), "FX_POL")

    def test_unrelated_is_other(self):
        self.assertEqual(classify("公司今天下午三点在会议室开产品讨论会，请大家准时参加"), "other")

    def test_country_marker_required(self):
        # 有货币政策词但没有国别标记，不应落进 US_MON
        self.assertNotEqual(classify("市场预计本次议息会议将维持利率不变"), "US_MON")


if __name__ == "__main__":
    unittest.main()
