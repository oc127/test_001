"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Table,
  Button,
  InputNumber,
  Upload,
  message,
  Space,
  Popconfirm,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { UploadProps } from "antd";
import { UploadOutlined, DownloadOutlined } from "@ant-design/icons";
import { getProducts, importProductsCsv } from "@/lib/api";
import api from "@/lib/api";

interface Product {
  id: number;
  sku: string;
  name: string;
  store_name: string;
  store_id: number;
  cogs_rmb: number;
  freight_rmb_per_unit: number;
  duty_rate_pct: number;
}

export default function ProductsSettingsPage() {
  const [data, setData] = useState<Product[]>([]);
  const [loading, setLoading] = useState(false);
  const [editingKey, setEditingKey] = useState<number | null>(null);
  const [editValues, setEditValues] = useState<Partial<Product>>({});

  const fetchProducts = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getProducts();
      setData(Array.isArray(res) ? res : res.items || []);
    } catch {
      message.error("加载产品列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProducts();
  }, [fetchProducts]);

  const startEdit = (record: Product) => {
    setEditingKey(record.id);
    setEditValues({
      cogs_rmb: record.cogs_rmb,
      freight_rmb_per_unit: record.freight_rmb_per_unit,
      duty_rate_pct: record.duty_rate_pct,
    });
  };

  const cancelEdit = () => {
    setEditingKey(null);
    setEditValues({});
  };

  const saveEdit = async (id: number) => {
    try {
      await api.put(`/products/${id}`, editValues);
      message.success("保存成功");
      setEditingKey(null);
      setEditValues({});
      fetchProducts();
    } catch {
      message.error("保存失败");
    }
  };

  const handleUpload: UploadProps["customRequest"] = async (options) => {
    const { file, onSuccess, onError } = options;
    try {
      await importProductsCsv(file as File);
      message.success("CSV导入成功");
      fetchProducts();
      onSuccess?.(null);
    } catch (err) {
      message.error("CSV导入失败");
      onError?.(err as Error);
    }
  };

  const downloadTemplate = () => {
    const headers = "sku,name,store_id,cogs_rmb,freight_rmb_per_unit,duty_rate_pct";
    const example = "SKU-001,示例产品,1,45.00,8.50,5.0";
    const csv = [headers, example].join("\n");
    const blob = new Blob(["\uFEFF" + csv], {
      type: "text/csv;charset=utf-8;",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "products_template.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  const columns: ColumnsType<Product> = [
    { title: "SKU", dataIndex: "sku", key: "sku", width: 140 },
    {
      title: "商品名",
      dataIndex: "name",
      key: "name",
      width: 200,
      ellipsis: true,
    },
    { title: "店铺", dataIndex: "store_name", key: "store_name", width: 120 },
    {
      title: "成本 (RMB)",
      dataIndex: "cogs_rmb",
      key: "cogs_rmb",
      width: 130,
      render: (val: number, record: Product) =>
        editingKey === record.id ? (
          <InputNumber
            value={editValues.cogs_rmb}
            min={0}
            step={0.01}
            onChange={(v) =>
              setEditValues((prev) => ({ ...prev, cogs_rmb: v ?? 0 }))
            }
            style={{ width: "100%" }}
          />
        ) : (
          `¥${val?.toFixed(2) ?? "0.00"}`
        ),
    },
    {
      title: "头程/单位 (RMB)",
      dataIndex: "freight_rmb_per_unit",
      key: "freight_rmb_per_unit",
      width: 150,
      render: (val: number, record: Product) =>
        editingKey === record.id ? (
          <InputNumber
            value={editValues.freight_rmb_per_unit}
            min={0}
            step={0.01}
            onChange={(v) =>
              setEditValues((prev) => ({
                ...prev,
                freight_rmb_per_unit: v ?? 0,
              }))
            }
            style={{ width: "100%" }}
          />
        ) : (
          `¥${val?.toFixed(2) ?? "0.00"}`
        ),
    },
    {
      title: "关税税率 (%)",
      dataIndex: "duty_rate_pct",
      key: "duty_rate_pct",
      width: 130,
      render: (val: number, record: Product) =>
        editingKey === record.id ? (
          <InputNumber
            value={editValues.duty_rate_pct}
            min={0}
            max={100}
            step={0.1}
            onChange={(v) =>
              setEditValues((prev) => ({ ...prev, duty_rate_pct: v ?? 0 }))
            }
            style={{ width: "100%" }}
          />
        ) : (
          `${val?.toFixed(1) ?? "0.0"}%`
        ),
    },
    {
      title: "操作",
      key: "actions",
      width: 160,
      render: (_: unknown, record: Product) =>
        editingKey === record.id ? (
          <Space>
            <Button type="link" size="small" onClick={() => saveEdit(record.id)}>
              保存
            </Button>
            <Popconfirm title="确定取消？" onConfirm={cancelEdit}>
              <Button type="link" size="small">
                取消
              </Button>
            </Popconfirm>
          </Space>
        ) : (
          <Button
            type="link"
            size="small"
            disabled={editingKey !== null}
            onClick={() => startEdit(record)}
          >
            编辑
          </Button>
        ),
    },
  ];

  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 16 }}>产品/成本管理</h2>

      <Space style={{ marginBottom: 16 }}>
        <Upload customRequest={handleUpload} showUploadList={false} accept=".csv">
          <Button icon={<UploadOutlined />}>导入CSV</Button>
        </Upload>
        <Button icon={<DownloadOutlined />} onClick={downloadTemplate}>
          下载模板
        </Button>
      </Space>

      <Table<Product>
        columns={columns}
        dataSource={data}
        loading={loading}
        rowKey="id"
        pagination={{
          pageSize: 20,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
        }}
        size="small"
      />
    </div>
  );
}
