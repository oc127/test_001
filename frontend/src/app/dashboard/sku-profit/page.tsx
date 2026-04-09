"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Table,
  DatePicker,
  Select,
  Button,
  Space,
  Row,
  Col,
  message,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { DownloadOutlined } from "@ant-design/icons";
import dayjs, { Dayjs } from "dayjs";
import { getProfitBySku, getStores } from "@/lib/api";

const { RangePicker } = DatePicker;

interface SkuProfit {
  sku: string;
  product_name: string;
  store_name: string;
  units_sold: number;
  revenue: number;
  amazon_fees: number;
  fba_fees: number;
  ad_spend: number;
  refunds: number;
  net_revenue_rmb: number;
  cogs_rmb: number;
  freight_rmb: number;
  duty_rmb: number;
  vat_rmb: number;
  net_profit_rmb: number;
  margin_pct: number;
}

interface Store {
  id: number;
  name: string;
}

export default function SkuProfitPage() {
  const [data, setData] = useState<SkuProfit[]>([]);
  const [stores, setStores] = useState<Store[]>([]);
  const [loading, setLoading] = useState(false);
  const [dateRange, setDateRange] = useState<
    [Dayjs | null, Dayjs | null] | null
  >(null);
  const [storeId, setStoreId] = useState<number | undefined>();
  const [category, setCategory] = useState<string | undefined>();

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = {};
      if (dateRange && dateRange[0] && dateRange[1]) {
        params.start_date = dateRange[0].format("YYYY-MM-DD");
        params.end_date = dateRange[1].format("YYYY-MM-DD");
      }
      if (storeId) params.store_id = storeId;
      if (category) params.category = category;

      const result = await getProfitBySku(params);
      setData(Array.isArray(result) ? result : result.items || []);
    } catch {
      message.error("加载SKU利润数据失败");
    } finally {
      setLoading(false);
    }
  }, [dateRange, storeId, category]);

  useEffect(() => {
    const fetchStores = async () => {
      try {
        const res = await getStores();
        setStores(Array.isArray(res) ? res : res.items || []);
      } catch {
        /* stores may not be loaded yet */
      }
    };
    fetchStores();
    fetchData();
  }, [fetchData]);

  const handleExportCsv = () => {
    if (data.length === 0) {
      message.warning("无数据可导出");
      return;
    }
    const headers = [
      "SKU",
      "商品名",
      "店铺",
      "销量",
      "营收",
      "Amazon费用",
      "FBA费用",
      "广告费",
      "退款",
      "净收入(RMB)",
      "成本(RMB)",
      "头程(RMB)",
      "关税(RMB)",
      "VAT(RMB)",
      "净利润(RMB)",
      "毛利率(%)",
    ];
    const rows = data.map((r) =>
      [
        r.sku,
        r.product_name,
        r.store_name,
        r.units_sold,
        r.revenue,
        r.amazon_fees,
        r.fba_fees,
        r.ad_spend,
        r.refunds,
        r.net_revenue_rmb,
        r.cogs_rmb,
        r.freight_rmb,
        r.duty_rmb,
        r.vat_rmb,
        r.net_profit_rmb,
        r.margin_pct,
      ].join(",")
    );
    const csv = [headers.join(","), ...rows].join("\n");
    const blob = new Blob(["\uFEFF" + csv], {
      type: "text/csv;charset=utf-8;",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `sku_profit_${dayjs().format("YYYYMMDD_HHmmss")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const moneyRender = (val: number) => (
    <span style={{ color: val >= 0 ? "#52c41a" : "#ff4d4f" }}>
      ¥{val?.toFixed(2) ?? "0.00"}
    </span>
  );

  const columns: ColumnsType<SkuProfit> = [
    {
      title: "SKU",
      dataIndex: "sku",
      key: "sku",
      fixed: "left",
      width: 140,
    },
    { title: "商品名", dataIndex: "product_name", key: "product_name", width: 180, ellipsis: true },
    { title: "店铺", dataIndex: "store_name", key: "store_name", width: 120 },
    {
      title: "销量",
      dataIndex: "units_sold",
      key: "units_sold",
      width: 80,
      sorter: (a, b) => a.units_sold - b.units_sold,
    },
    {
      title: "营收",
      dataIndex: "revenue",
      key: "revenue",
      width: 100,
      render: (v: number) => `$${v?.toFixed(2) ?? "0.00"}`,
      sorter: (a, b) => a.revenue - b.revenue,
    },
    {
      title: "Amazon费用",
      dataIndex: "amazon_fees",
      key: "amazon_fees",
      width: 110,
      render: (v: number) => `$${v?.toFixed(2) ?? "0.00"}`,
    },
    {
      title: "FBA费用",
      dataIndex: "fba_fees",
      key: "fba_fees",
      width: 100,
      render: (v: number) => `$${v?.toFixed(2) ?? "0.00"}`,
    },
    {
      title: "广告费",
      dataIndex: "ad_spend",
      key: "ad_spend",
      width: 100,
      render: (v: number) => `$${v?.toFixed(2) ?? "0.00"}`,
    },
    {
      title: "退款",
      dataIndex: "refunds",
      key: "refunds",
      width: 80,
      render: (v: number) => `$${v?.toFixed(2) ?? "0.00"}`,
    },
    {
      title: "净收入(RMB)",
      dataIndex: "net_revenue_rmb",
      key: "net_revenue_rmb",
      width: 120,
      render: moneyRender,
      sorter: (a, b) => a.net_revenue_rmb - b.net_revenue_rmb,
    },
    {
      title: "成本(RMB)",
      dataIndex: "cogs_rmb",
      key: "cogs_rmb",
      width: 110,
      render: (v: number) => `¥${v?.toFixed(2) ?? "0.00"}`,
    },
    {
      title: "头程(RMB)",
      dataIndex: "freight_rmb",
      key: "freight_rmb",
      width: 110,
      render: (v: number) => `¥${v?.toFixed(2) ?? "0.00"}`,
    },
    {
      title: "关税(RMB)",
      dataIndex: "duty_rmb",
      key: "duty_rmb",
      width: 100,
      render: (v: number) => `¥${v?.toFixed(2) ?? "0.00"}`,
    },
    {
      title: "VAT(RMB)",
      dataIndex: "vat_rmb",
      key: "vat_rmb",
      width: 100,
      render: (v: number) => `¥${v?.toFixed(2) ?? "0.00"}`,
    },
    {
      title: "净利润(RMB)",
      dataIndex: "net_profit_rmb",
      key: "net_profit_rmb",
      width: 130,
      render: moneyRender,
      sorter: (a, b) => a.net_profit_rmb - b.net_profit_rmb,
    },
    {
      title: "毛利率",
      dataIndex: "margin_pct",
      key: "margin_pct",
      width: 100,
      fixed: "right",
      render: (v: number) => (
        <Tag color={v >= 0 ? "green" : "red"}>{v?.toFixed(1) ?? "0.0"}%</Tag>
      ),
      sorter: (a, b) => a.margin_pct - b.margin_pct,
    },
  ];

  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 16 }}>SKU利润分析</h2>

      {/* Filters */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col>
          <RangePicker
            onChange={(dates) =>
              setDateRange(dates as [Dayjs | null, Dayjs | null] | null)
            }
            placeholder={["开始日期", "结束日期"]}
          />
        </Col>
        <Col>
          <Select
            style={{ width: 180 }}
            placeholder="选择店铺"
            allowClear
            onChange={(val) => setStoreId(val)}
            options={stores.map((s) => ({ label: s.name, value: s.id }))}
          />
        </Col>
        <Col>
          <Select
            style={{ width: 140 }}
            placeholder="品类"
            allowClear
            onChange={(val) => setCategory(val)}
            options={[
              { label: "全部", value: "" },
              { label: "电子产品", value: "electronics" },
              { label: "家居", value: "home" },
              { label: "服装", value: "apparel" },
              { label: "其他", value: "other" },
            ]}
          />
        </Col>
        <Col>
          <Space>
            <Button type="primary" onClick={fetchData}>
              查询
            </Button>
            <Button icon={<DownloadOutlined />} onClick={handleExportCsv}>
              导出CSV
            </Button>
          </Space>
        </Col>
      </Row>

      {/* Data Table */}
      <Table<SkuProfit>
        columns={columns}
        dataSource={data}
        loading={loading}
        rowKey="sku"
        scroll={{ x: 1800 }}
        pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
        size="small"
      />
    </div>
  );
}
