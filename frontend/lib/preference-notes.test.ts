/**
 * preference-notes.ts 单测——"这次对话学到的"笔记体文案生成（用户拍板：
 * 第一人称/口语/具体/无计数/≤20字/禁"为您智能贴心"/禁 emoji/禁排比句式）。
 */

import { describe, expect, it } from "vitest";

import { distanceNote, preferenceNote } from "./preference-notes";

describe("preferenceNote", () => {
  it("空数组返回 null（由调用方走空态）", () => {
    expect(preferenceNote([])).toBeNull();
  });

  it("取最靠前的 tag 生成一句笔记（不含计数数字）", () => {
    const note = preferenceNote(["有包间", "低脂"]);
    expect(note).not.toBeNull();
    expect(note).not.toMatch(/\d+\s*次/); // 不含"×N 次"这类频次计数
    expect(note!.length).toBeLessThanOrEqual(20);
  });

  it("词典外 tag（理论上不该出现）走兜底模板，不崩", () => {
    const note = preferenceNote(["一个不存在的tag"]); // eslint-disable-line
    expect(note).toContain("一个不存在的tag");
  });

  it("禁用词不出现在任何已知 tag 笔记里", () => {
    const banned = ["为您", "智能", "贴心"];
    const allTags = [
      "亲子友好", "低强度", "可休息", "无台阶", "无障碍", "适合 5-10 岁",
      "适合老人", "适合青少年", "高强度", "下午茶", "不辣", "低脂",
      "健康轻食", "无牛肉", "日料", "有儿童餐", "有包间", "甜品", "粤菜",
      "软烂", "高人均", "高蛋白", "亲密情侣", "商务体面", "学习成长",
      "安静聊天", "室内", "户外", "拍照友好", "热闹", "独处舒缓", "看展",
      "礼仪感", "社交", "网红打卡",
    ];
    for (const tag of allTags) {
      const note = preferenceNote([tag]);
      expect(note).not.toBeNull();
      for (const word of banned) {
        expect(note).not.toContain(word);
      }
    }
  });
});

describe("distanceNote", () => {
  it("null/undefined 返回 null", () => {
    expect(distanceNote(null)).toBeNull();
    expect(distanceNote(undefined)).toBeNull();
  });

  it("生成含公里数的口语短句", () => {
    expect(distanceNote(3)).toBe("最近都定在 3km 内");
  });

  it("非整数保留一位小数", () => {
    expect(distanceNote(3.25)).toBe("最近都定在 3.3km 内");
  });
});
