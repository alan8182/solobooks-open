/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 电子税务局官网地址解析。
 *
 * 全国统一规范电子税务局没有统一的登录地址：按省分设子域名
 * `etax.<省拼音>.chinatax.gov.cn`，另有 5 个计划单列市（深圳/宁波/厦门/青岛/大连）
 * 使用自己的子域名，不跟所属省份共用。所以「去电子税务局」必须先知道公司注册地，
 * 由这里唯一映射一次——页面和指引都只调用本文件，避免两处各写一份省份表。
 *
 * 映射口径（2026-11 实测域名均可解析）：
 * - 省份取营业执照上的登记机关所在省；
 * - 注册在计划单列市的公司，必须选到市级，否则会跳到省局站点、登录后找不到本企业。
 */

export interface EtaxRegion {
  /** 唯一键：省 / 省|市，存进主体档案的是 province + city 两个字段 */
  key: string
  province: string
  /** 计划单列市；省本级为空字符串 */
  city: string
  /** 下拉与提示里的显示名，如「浙江省 · 宁波市」 */
  label: string
  subdomain: string
}

const PROVINCES: Array<[string, string]> = [
  ['北京市', 'beijing'],
  ['天津市', 'tianjin'],
  ['河北省', 'hebei'],
  ['山西省', 'shanxi'],
  ['内蒙古自治区', 'neimenggu'],
  ['辽宁省', 'liaoning'],
  ['吉林省', 'jilin'],
  ['黑龙江省', 'heilongjiang'],
  ['上海市', 'shanghai'],
  ['江苏省', 'jiangsu'],
  ['浙江省', 'zhejiang'],
  ['安徽省', 'anhui'],
  ['福建省', 'fujian'],
  ['江西省', 'jiangxi'],
  ['山东省', 'shandong'],
  ['河南省', 'henan'],
  ['湖北省', 'hubei'],
  ['湖南省', 'hunan'],
  ['广东省', 'guangdong'],
  ['广西壮族自治区', 'guangxi'],
  ['海南省', 'hainan'],
  ['重庆市', 'chongqing'],
  ['四川省', 'sichuan'],
  ['贵州省', 'guizhou'],
  ['云南省', 'yunnan'],
  ['西藏自治区', 'xizang'],
  ['陕西省', 'shaanxi'],
  ['甘肃省', 'gansu'],
  ['青海省', 'qinghai'],
  ['宁夏回族自治区', 'ningxia'],
  ['新疆维吾尔自治区', 'xinjiang'],
]

/** 计划单列市：省局与市局是两套站点 */
const SEPARATE_CITIES: Array<[string, string, string]> = [
  ['辽宁省', '大连市', 'dalian'],
  ['浙江省', '宁波市', 'ningbo'],
  ['福建省', '厦门市', 'xiamen'],
  ['山东省', '青岛市', 'qingdao'],
  ['广东省', '深圳市', 'shenzhen'],
]

export const ETAX_REGIONS: EtaxRegion[] = [
  ...PROVINCES.map<EtaxRegion>(([province, subdomain]) => ({
    key: province,
    province,
    city: '',
    label: province,
    subdomain,
  })),
  ...SEPARATE_CITIES.map<EtaxRegion>(([province, city, subdomain]) => ({
    key: `${province}|${city}`,
    province,
    city,
    label: `${province} · ${city}`,
    subdomain,
  })),
]

export function etaxRegionKey(province?: string | null, city?: string | null): string {
  const p = String(province || '').trim()
  const c = String(city || '').trim()
  return c ? `${p}|${c}` : p
}

/** 按注册地取电子税务局首页；注册地缺失或不在表里返回 null（调用方回落到省份地图）。 */
export function etaxPortalUrl(province?: string | null, city?: string | null): string | null {
  const key = etaxRegionKey(province, city)
  if (!key) return null
  const region = ETAX_REGIONS.find((item) => item.key === key)
  return region ? `https://etax.${region.subdomain}.chinatax.gov.cn` : null
}

/** 省级名去掉行政区划后缀，用于匹配执照住所里不带「省」的写法（「浙江杭州……」）。 */
function provinceAlias(province: string): string {
  return province.replace(/(壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区|省|市)$/, '')
}

/**
 * 从营业执照住所里认出注册地键（`省` / `省|市`）；认不出返回空字符串。
 *
 * 计划单列市优先：地址里同时出现「广东省」和「深圳市」时，要按市局站点落户，
 * 不能落到省局。只认表里的 36 个注册地，其余（如港澳台、境外）交给用户手选。
 */
export function etaxRegionFromAddress(address?: string | null): string {
  const text = String(address || '').replace(/\s+/g, '')
  if (!text) return ''
  for (const [province, city, _subdomain] of SEPARATE_CITIES) {
    if (text.includes(city) || text.includes(city.replace(/市$/, ''))) {
      return `${province}|${city}`
    }
  }
  for (const [province] of PROVINCES) {
    if (text.includes(province) || text.includes(provinceAlias(province))) return province
  }
  return ''
}

/**
 * 统一社会信用代码里第 3~8 位是登记机关行政区划码（GB 32100-2015），
 * 前两位即省级代码。执照照片里的住所可能漏扫或残缺，税号却几乎不会缺，
 * 所以注册地认不出时用它兜底。
 */
const PROVINCE_CODES: Record<string, string> = {
  '11': '北京市', '12': '天津市', '13': '河北省', '14': '山西省', '15': '内蒙古自治区',
  '21': '辽宁省', '22': '吉林省', '23': '黑龙江省',
  '31': '上海市', '32': '江苏省', '33': '浙江省', '34': '安徽省', '35': '福建省',
  '36': '江西省', '37': '山东省',
  '41': '河南省', '42': '湖北省', '43': '湖南省', '44': '广东省', '45': '广西壮族自治区',
  '46': '海南省',
  '50': '重庆市', '51': '四川省', '52': '贵州省', '53': '云南省', '54': '西藏自治区',
  '61': '陕西省', '62': '甘肃省', '63': '青海省', '64': '宁夏回族自治区', '65': '新疆维吾尔自治区',
}

/** 计划单列市的行政区划码前缀（前 4 位），与省局站点分开 */
const SEPARATE_CITY_CODES: Record<string, [string, string]> = {
  '2102': ['辽宁省', '大连市'],
  '3302': ['浙江省', '宁波市'],
  '3502': ['福建省', '厦门市'],
  '3702': ['山东省', '青岛市'],
  '4403': ['广东省', '深圳市'],
}

/**
 * 从 18 位统一社会信用代码的行政区划码认出注册地键；认不出返回空字符串。
 *
 * 只认 18 位信用代码：老版 15/17/20 位号段的位置含义不同，不能按同一口径切。
 */
export function etaxRegionFromTaxId(taxId?: string | null): string {
  const code = String(taxId || '').trim().toUpperCase()
  if (code.length !== 18) return ''
  const division = code.slice(2, 8)
  if (!/^\d{6}$/.test(division)) return ''
  const city = SEPARATE_CITY_CODES[division.slice(0, 4)]
  if (city) return `${city[0]}|${city[1]}`
  return PROVINCE_CODES[division.slice(0, 2)] || ''
}

/** 注册地的可读名称，用于按钮提示，如「浙江省 · 宁波市电子税务局」。 */
export function etaxRegionLabel(province?: string | null, city?: string | null): string | null {
  const key = etaxRegionKey(province, city)
  return ETAX_REGIONS.find((item) => item.key === key)?.label ?? null
}

/** 新建/编辑主体表单的选项：按省分组，省本级在前、计划单列市在后。 */
/**
 * 新建/编辑主体表单的选项：扁平列表，省本级与计划单列市用显示名区分
 * （「浙江省」/「浙江省 · 宁波市」）。
 *
 * 不用分组：分组标题 + 子项会把同一串地名显示两遍，且选中后输入框只显示子项
 * 文案（「省税务局」），看不出是哪个省。扁平列表选中态始终是完整地名。
 */
export function etaxRegionSelectOptions(): Array<{ label: string; value: string }> {
  return ETAX_REGIONS.map((item) => ({ label: item.label, value: item.key }))
}
