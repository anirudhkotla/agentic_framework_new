"use client";

import { Search } from "lucide-react";
import { useState } from "react";
import { categoryIcon } from "../lib/icons";

type Option = {
  id: string;
  name: string;
  description?: string;
  category?: string;
  meta?: string;
};

export function MultiChoice({
  title,
  options,
  selected,
  onChange,
  placeholder,
}: {
  title: string;
  options: Option[];
  selected: string[];
  onChange: (ids: string[]) => void;
  placeholder: string;
}) {
  const toggle = (id: string) => {
    if (selected.includes(id)) {
      onChange(selected.filter((item) => item !== id));
    } else {
      onChange([...selected, id]);
    }
  };

  return (
    <FilterableChoiceList
      title={title}
      options={options}
      selected={selected}
      onToggle={toggle}
      placeholder={placeholder}
    />
  );
}

function FilterableChoiceList({
  title,
  options,
  selected,
  onToggle,
  placeholder,
}: {
  title: string;
  options: Option[];
  selected: string[];
  onToggle: (id: string) => void;
  placeholder: string;
}) {
  const [query, setQuery] = useChoiceQuery();
  const filtered = options.filter((option) => {
    const haystack = `${option.name} ${option.description || ""} ${option.category || ""} ${option.meta || ""}`.toLowerCase();
    return haystack.includes(query.toLowerCase());
  });

  return (
    <div className="field">
      <label>{title}</label>
      <div style={{ position: "relative" }}>
        <Search size={16} style={{ position: "absolute", left: 10, top: 11, color: "var(--muted)" }} />
        <input
          className="input"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={placeholder}
          style={{ paddingLeft: 34 }}
        />
      </div>
      <div className="choice-list">
        {filtered.map((option) => {
          const Icon = categoryIcon(option.category || "custom");
          return (
            <label className="choice" key={option.id}>
              <input
                type="checkbox"
                checked={selected.includes(option.id)}
                onChange={() => onToggle(option.id)}
              />
              <span className="icon-tile" style={{ width: 28, height: 28 }}>
                <Icon size={15} />
              </span>
              <span>
                <strong>{option.name}</strong>
                <span>{option.meta || option.description || option.id}</span>
              </span>
            </label>
          );
        })}
        {filtered.length === 0 && <div className="notice">No matches for this search.</div>}
      </div>
    </div>
  );
}

function useChoiceQuery(): [string, (query: string) => void] {
  return useState("");
}
