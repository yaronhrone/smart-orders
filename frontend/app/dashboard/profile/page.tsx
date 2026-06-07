"use client";

import { useEffect, useState } from "react";
import { fetchMe } from "../../lib/api";

const REGION_LABEL: Record<string, string> = {
  tel_aviv: "תל אביב",
  jerusalem: "ירושלים",
  haifa: "חיפה",
  south: "דרום",
  north: "צפון",
  center: "מרכז",
};

export default function ProfilePage() {
  const [profile, setProfile] = useState<{
    company_name?: string | null;
    company_phone?: string | null;
    company_address?: string | null;
    phone?: string | null;
    position?: string | null;
    region?: string | null;
  } | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchMe()
      .then((me) => {
        setName(`${me.first_name} ${me.last_name}`.trim());
        setEmail(me.email ?? "");
        setProfile(me.profile ?? null);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-gray-500">טוען...</p>
      </div>
    );
  }

  return (
    <div className="px-6 py-6 max-w-lg">
      <h1 className="text-xl font-bold text-gray-800 mb-6">פרופיל חברה</h1>

      <div className="bg-white rounded-2xl shadow-sm p-6 space-y-4" dir="rtl">
        <Row label="שם" value={name} />
        <Row label="אימייל" value={email} />
        <hr className="border-gray-100" />
        <Row label="שם חברה / מסעדה" value={profile?.company_name} />
        <Row label="טלפון חברה" value={profile?.company_phone} />
        <Row label="כתובת למשלוח" value={profile?.company_address} />
        <Row label="טלפון אישי (WhatsApp)" value={profile?.phone} />
        <Row label="תפקיד" value={profile?.position} />
        <Row
          label="אזור"
          value={profile?.region ? (REGION_LABEL[profile.region] ?? profile.region) : undefined}
        />

        <p className="text-xs text-gray-400 pt-2">
          לשינוי פרטים — פנה/י למנהל המערכת.
        </p>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex gap-4 text-sm">
      <span className="w-44 shrink-0 text-gray-500">{label}</span>
      <span className="text-gray-800 font-medium">{value || "—"}</span>
    </div>
  );
}
